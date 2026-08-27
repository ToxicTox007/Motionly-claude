package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"go.temporal.io/server/common/api"
	"go.temporal.io/server/common/authorization"
	"go.temporal.io/server/common/config"
	"go.temporal.io/server/common/dynamicconfig"
	"go.temporal.io/server/common/log"
	_ "go.temporal.io/server/common/persistence/sql/sqlplugin/postgresql"
	"go.temporal.io/server/temporal"
	"go.uber.org/automaxprocs/maxprocs"
)

const (
	defaultConfigTemplate = "/opt/motionly-temporal/config/production.yaml.tmpl"
	temporalDBPassword    = "/run/secrets/temporal_db_password"
	maxBootstrapWindow    = 24 * time.Hour
)

var knownIdentityRoles = map[string]authorization.Role{
	"motionly-api":             authorization.RoleReader | authorization.RoleWriter,
	"motionly-dispatcher":      authorization.RoleReader | authorization.RoleWriter,
	"motionly-temporal-worker": authorization.RoleWorker | authorization.RoleReader,
	"motionly-reconciler":      authorization.RoleReader | authorization.RoleWriter,
}

type temporalPolicy struct {
	namespace      string
	bootstrapUntil time.Time
}

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "motionly temporal server:", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) == 0 || args[0] != "start" {
		return errors.New("only the start command is supported")
	}
	flags := flag.NewFlagSet("start", flag.ContinueOnError)
	flags.SetOutput(os.Stderr)
	configTemplate := flags.String("config-template", defaultConfigTemplate, "static Temporal config template")
	zone := flags.String("zone", "", "availability zone")
	if err := flags.Parse(args[1:]); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return errors.New("start does not accept positional arguments")
	}
	if _, err := maxprocs.Set(); err != nil {
		fmt.Fprintln(os.Stderr, "warning: unable to set GOMAXPROCS:", err)
	}

	policy, err := policyFromEnvironment(time.Now().UTC())
	if err != nil {
		return err
	}
	configDir, cleanup, err := renderConfig(*configTemplate, temporalDBPassword)
	if err != nil {
		return err
	}
	defer cleanup()
	serverConfig, err := config.LoadConfig("production", configDir, *zone)
	if err != nil {
		return fmt.Errorf("load rendered Temporal config: %w", err)
	}
	logger := log.NewZapLogger(log.BuildZapLogger(serverConfig.Log))

	var dynamicConfigClient dynamicconfig.Client
	if serverConfig.DynamicConfigClient != nil {
		dynamicConfigClient, err = dynamicconfig.NewFileBasedClient(
			serverConfig.DynamicConfigClient,
			logger,
			temporal.InterruptCh(),
		)
		if err != nil {
			return fmt.Errorf("create dynamic config client: %w", err)
		}
	} else {
		dynamicConfigClient = dynamicconfig.NewNoopClient()
	}

	server, err := temporal.NewServer(
		temporal.ForServices(temporal.DefaultServices),
		temporal.WithConfig(serverConfig),
		temporal.WithDynamicConfigClient(dynamicConfigClient),
		temporal.WithLogger(logger),
		temporal.InterruptOn(temporal.InterruptCh()),
		temporal.WithClaimMapper(func(_ *config.Config) authorization.ClaimMapper {
			return mtlsClaimMapper{policy: policy}
		}),
		temporal.WithAuthorizer(mtlsAuthorizer{policy: policy}),
	)
	if err != nil {
		return fmt.Errorf("create Temporal server: %w", err)
	}
	if err := server.Start(); err != nil {
		return fmt.Errorf("start Temporal server: %w", err)
	}
	return nil
}

func policyFromEnvironment(now time.Time) (temporalPolicy, error) {
	namespace := strings.TrimSpace(os.Getenv("MOTIONLY_TEMPORAL_NAMESPACE"))
	if namespace == "" {
		return temporalPolicy{}, errors.New("MOTIONLY_TEMPORAL_NAMESPACE is required")
	}
	value := strings.TrimSpace(os.Getenv("MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL"))
	if value == "" {
		return temporalPolicy{}, errors.New("MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL is required")
	}
	bootstrapUntil, err := time.Parse(time.RFC3339, value)
	if err != nil {
		return temporalPolicy{}, errors.New("MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL must be RFC3339")
	}
	bootstrapUntil = bootstrapUntil.UTC()
	if !bootstrapUntil.After(now) || bootstrapUntil.Sub(now) > maxBootstrapWindow {
		return temporalPolicy{}, errors.New("bootstrap admin certificate window must be within the next 24 hours")
	}
	return temporalPolicy{namespace: namespace, bootstrapUntil: bootstrapUntil}, nil
}

// mtlsClaimMapper maps only the authenticated peer certificate's common name.
// No token, client-provided identity, or untrusted metadata can grant a role.
type mtlsClaimMapper struct {
	policy temporalPolicy
}

func (m mtlsClaimMapper) AuthInfoRequired() bool {
	return true
}

func (m mtlsClaimMapper) GetClaims(authInfo *authorization.AuthInfo) (*authorization.Claims, error) {
	if authInfo == nil || authInfo.TLSSubject == nil {
		return nil, errors.New("mTLS client certificate is required")
	}
	identity := strings.TrimSpace(authInfo.TLSSubject.CommonName)
	if role, ok := knownIdentityRoles[identity]; ok {
		return &authorization.Claims{
			Subject:    identity,
			Namespaces: map[string]authorization.Role{m.policy.namespace: role},
		}, nil
	}
	if identity == "motionly-diagnostics" {
		return &authorization.Claims{Subject: identity}, nil
	}
	if identity == "motionly-namespace-bootstrap" && time.Now().UTC().Before(m.policy.bootstrapUntil) {
		return &authorization.Claims{Subject: identity, System: authorization.RoleAdmin}, nil
	}
	return nil, errors.New("unrecognized or expired Temporal mTLS identity")
}

type mtlsAuthorizer struct {
	policy temporalPolicy
}

func (a mtlsAuthorizer) Authorize(
	_ context.Context,
	claims *authorization.Claims,
	target *authorization.CallTarget,
) (authorization.Result, error) {
	if claims == nil || target == nil || !isKnownSubject(claims.Subject) {
		return deny(), nil
	}
	if isHealthAPI(target.APIName) {
		return allow(), nil
	}
	if isOperatorOrAdminAPI(target.APIName) {
		if claims.Subject == "motionly-namespace-bootstrap" && claims.System&authorization.RoleAdmin != 0 {
			return allow(), nil
		}
		return deny(), nil
	}
	if target.Namespace != "" && target.Namespace != a.policy.namespace {
		return deny(), nil
	}
	if claims.Subject == "motionly-namespace-bootstrap" {
		return deny(), nil
	}
	if isSafeClusterReadAPI(target.APIName) {
		if claims.Subject == "motionly-diagnostics" || target.APIName == "/temporal.api.workflowservice.v1.WorkflowService/GetSystemInfo" {
			return allow(), nil
		}
		return deny(), nil
	}
	if target.Namespace == "" || claims.Subject == "motionly-diagnostics" {
		return deny(), nil
	}

	role := claims.Namespaces[a.policy.namespace]
	if role&authorization.RoleWorker != 0 && isWorkerAPI(target.APIName) {
		return allow(), nil
	}
	metadata := api.GetMethodMetadata(target.APIName)
	switch metadata.Access {
	case api.AccessReadOnly:
		if role&authorization.RoleReader != 0 {
			return allow(), nil
		}
	case api.AccessWrite:
		if role&authorization.RoleWriter != 0 {
			return allow(), nil
		}
	}
	return deny(), nil
}

func isKnownSubject(subject string) bool {
	if subject == "motionly-diagnostics" || subject == "motionly-namespace-bootstrap" {
		return true
	}
	_, ok := knownIdentityRoles[subject]
	return ok
}

func isOperatorOrAdminAPI(name string) bool {
	return strings.Contains(name, ".OperatorService/") ||
		strings.Contains(name, ".AdminService/") ||
		strings.HasSuffix(name, "/RegisterNamespace") ||
		strings.HasSuffix(name, "/UpdateNamespace") ||
		strings.HasSuffix(name, "/DeprecateNamespace") ||
		strings.HasSuffix(name, "/DeleteNamespace")
}

func isHealthAPI(name string) bool {
	return strings.Contains(name, "grpc.health.v1.Health/") || strings.HasSuffix(name, "/CheckHealth")
}

func isSafeClusterReadAPI(name string) bool {
	return name == "/temporal.api.workflowservice.v1.WorkflowService/GetSystemInfo" ||
		name == "/temporal.api.workflowservice.v1.WorkflowService/GetClusterInfo"
}

func isWorkerAPI(name string) bool {
	return strings.HasSuffix(name, "/PollWorkflowTaskQueue") ||
		strings.HasSuffix(name, "/PollActivityTaskQueue") ||
		strings.HasSuffix(name, "/PollNexusTaskQueue") ||
		strings.HasSuffix(name, "/RespondWorkflowTaskCompleted") ||
		strings.HasSuffix(name, "/RespondWorkflowTaskFailed") ||
		strings.HasSuffix(name, "/RespondActivityTaskCompleted") ||
		strings.HasSuffix(name, "/RespondActivityTaskFailed") ||
		strings.HasSuffix(name, "/RespondActivityTaskCanceled") ||
		strings.HasSuffix(name, "/RecordActivityTaskHeartbeat") ||
		strings.HasSuffix(name, "/RecordActivityTaskHeartbeatById")
}

func allow() authorization.Result {
	return authorization.Result{Decision: authorization.DecisionAllow}
}

func deny() authorization.Result {
	return authorization.Result{Decision: authorization.DecisionDeny}
}

func renderConfig(templatePath string, passwordPath string) (string, func(), error) {
	template, err := os.ReadFile(templatePath)
	if err != nil {
		return "", nil, fmt.Errorf("read config template: %w", err)
	}
	password, err := os.ReadFile(passwordPath)
	if err != nil {
		return "", nil, fmt.Errorf("read Temporal database secret: %w", err)
	}
	passwordText := strings.TrimSuffix(string(password), "\n")
	if passwordText == "" || strings.ContainsAny(passwordText, "\r\n") {
		return "", nil, errors.New("Temporal database secret must be one non-empty line")
	}
	const marker = "{{TEMPORAL_DB_PASSWORD}}"
	if !strings.Contains(string(template), marker) {
		return "", nil, errors.New("Temporal config template lacks database-password marker")
	}
	configDir, err := os.MkdirTemp("", "motionly-temporal-config-")
	if err != nil {
		return "", nil, fmt.Errorf("create private config directory: %w", err)
	}
	cleanup := func() { _ = os.RemoveAll(configDir) }
	if err := os.Chmod(configDir, 0o700); err != nil {
		cleanup()
		return "", nil, fmt.Errorf("protect config directory: %w", err)
	}
	rendered := strings.ReplaceAll(string(template), marker, strconv.Quote(passwordText))
	output := filepath.Join(configDir, "production.yaml")
	if err := os.WriteFile(output, []byte(rendered), 0o600); err != nil {
		cleanup()
		return "", nil, fmt.Errorf("write private Temporal config: %w", err)
	}
	return configDir, cleanup, nil
}
