package main

import (
	"context"
	"crypto/x509/pkix"
	"os"
	"path/filepath"
	"testing"
	"time"

	"go.temporal.io/server/common/authorization"
	"go.temporal.io/server/common/config"
)

const testNamespace = "motionly"

func TestMTLSClaimMapperMapsOnlyKnownCertificateSubjects(t *testing.T) {
	policy := temporalPolicy{namespace: testNamespace, bootstrapUntil: time.Now().Add(time.Hour)}
	mapper := mtlsClaimMapper{policy: policy}
	cases := []struct {
		identity string
		role     authorization.Role
	}{
		{"motionly-api", authorization.RoleReader | authorization.RoleWriter},
		{"motionly-dispatcher", authorization.RoleReader | authorization.RoleWriter},
		{"motionly-temporal-worker", authorization.RoleWorker | authorization.RoleReader},
		{"motionly-reconciler", authorization.RoleReader | authorization.RoleWriter},
	}
	for _, testCase := range cases {
		t.Run(testCase.identity, func(t *testing.T) {
			claims, err := mapper.GetClaims(&authorization.AuthInfo{
				TLSSubject: &pkix.Name{CommonName: testCase.identity},
			})
			if err != nil {
				t.Fatal(err)
			}
			if claims.Subject != testCase.identity || claims.Namespaces[testNamespace] != testCase.role {
				t.Fatalf("unexpected claims: %#v", claims)
			}
		})
	}
	for _, identity := range []string{"", "motionly-admin", "motionly-api-copy"} {
		if _, err := mapper.GetClaims(&authorization.AuthInfo{
			TLSSubject: &pkix.Name{CommonName: identity},
		}); err == nil {
			t.Fatalf("identity %q was unexpectedly accepted", identity)
		}
	}
}

func TestBootstrapAdminExpiresAndDiagnosticsHasNoNamespaceRoles(t *testing.T) {
	now := time.Now()
	active := mtlsClaimMapper{policy: temporalPolicy{namespace: testNamespace, bootstrapUntil: now.Add(time.Minute)}}
	claims, err := active.GetClaims(&authorization.AuthInfo{
		TLSSubject: &pkix.Name{CommonName: "motionly-namespace-bootstrap"},
	})
	if err != nil || claims.System != authorization.RoleAdmin {
		t.Fatalf("expected bounded bootstrap admin claim, got %#v, %v", claims, err)
	}
	expired := mtlsClaimMapper{policy: temporalPolicy{namespace: testNamespace, bootstrapUntil: now.Add(-time.Minute)}}
	if _, err := expired.GetClaims(&authorization.AuthInfo{
		TLSSubject: &pkix.Name{CommonName: "motionly-namespace-bootstrap"},
	}); err == nil {
		t.Fatal("expired bootstrap certificate identity was accepted")
	}
	diagnostics, err := active.GetClaims(&authorization.AuthInfo{
		TLSSubject: &pkix.Name{CommonName: "motionly-diagnostics"},
	})
	if err != nil || diagnostics.System != authorization.RoleUndefined || len(diagnostics.Namespaces) != 0 {
		t.Fatalf("diagnostics must not receive namespace privileges: %#v, %v", diagnostics, err)
	}
}

func TestAuthorizerEnforcesNamespaceRoleAndAdminBoundary(t *testing.T) {
	authorizer := mtlsAuthorizer{policy: temporalPolicy{namespace: testNamespace}}
	apiClaims := &authorization.Claims{
		Subject: "motionly-api",
		Namespaces: map[string]authorization.Role{
			testNamespace: authorization.RoleReader | authorization.RoleWriter,
		},
	}
	workerClaims := &authorization.Claims{
		Subject: "motionly-temporal-worker",
		Namespaces: map[string]authorization.Role{
			testNamespace: authorization.RoleWorker | authorization.RoleReader,
		},
	}
	bootstrapClaims := &authorization.Claims{
		Subject: "motionly-namespace-bootstrap",
		System:  authorization.RoleAdmin,
	}
	diagnosticsClaims := &authorization.Claims{Subject: "motionly-diagnostics"}

	assertDecision(t, authorizer, apiClaims, "/temporal.api.workflowservice.v1.WorkflowService/StartWorkflowExecution", testNamespace, authorization.DecisionAllow)
	assertDecision(t, authorizer, apiClaims, "/temporal.api.workflowservice.v1.WorkflowService/DescribeWorkflowExecution", "other", authorization.DecisionDeny)
	assertDecision(t, authorizer, apiClaims, "/temporal.api.operatorservice.v1.OperatorService/ListSearchAttributes", "", authorization.DecisionDeny)
	assertDecision(t, authorizer, workerClaims, "/temporal.api.workflowservice.v1.WorkflowService/PollWorkflowTaskQueue", testNamespace, authorization.DecisionAllow)
	assertDecision(t, authorizer, workerClaims, "/temporal.api.workflowservice.v1.WorkflowService/StartWorkflowExecution", testNamespace, authorization.DecisionDeny)
	assertDecision(t, authorizer, diagnosticsClaims, "/grpc.health.v1.Health/Check", "", authorization.DecisionAllow)
	assertDecision(t, authorizer, diagnosticsClaims, "/temporal.api.workflowservice.v1.WorkflowService/DescribeWorkflowExecution", testNamespace, authorization.DecisionDeny)
	assertDecision(t, authorizer, bootstrapClaims, "/temporal.api.operatorservice.v1.OperatorService/CreateNamespace", "", authorization.DecisionAllow)
	assertDecision(t, authorizer, bootstrapClaims, "/temporal.api.workflowservice.v1.WorkflowService/StartWorkflowExecution", testNamespace, authorization.DecisionDeny)
}

func TestPolicyFromEnvironmentRequiresBoundedBootstrapWindow(t *testing.T) {
	t.Setenv("MOTIONLY_TEMPORAL_NAMESPACE", testNamespace)
	t.Setenv("MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL", time.Now().UTC().Add(time.Hour).Format(time.RFC3339))
	if _, err := policyFromEnvironment(time.Now().UTC()); err != nil {
		t.Fatal(err)
	}
	t.Setenv("MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL", time.Now().UTC().Add(25*time.Hour).Format(time.RFC3339))
	if _, err := policyFromEnvironment(time.Now().UTC()); err == nil {
		t.Fatal("bootstrap window over 24h was accepted")
	}
}

func assertDecision(
	t *testing.T,
	authorizer mtlsAuthorizer,
	claims *authorization.Claims,
	apiName string,
	namespace string,
	expected authorization.Decision,
) {
	t.Helper()
	result, err := authorizer.Authorize(context.Background(), claims, &authorization.CallTarget{
		APIName: apiName, Namespace: namespace,
	})
	if err != nil || result.Decision != expected {
		t.Fatalf("%s expected %v, got %#v, %v", apiName, expected, result, err)
	}
}

func TestProductionConfigTemplateLoadsMandatoryFrontendAndInternodeMTLS(t *testing.T) {
	temporary := t.TempDir()
	passwordPath := filepath.Join(temporary, "temporal_db_password")
	if err := os.WriteFile(passwordPath, []byte("test-database-password\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	configDir, cleanup, err := renderConfig(
		filepath.Join("..", "..", "..", "config", "production.yaml.tmpl"),
		passwordPath,
	)
	if err != nil {
		t.Fatal(err)
	}
	defer cleanup()
	loaded, err := config.LoadConfig("production", configDir, "")
	if err != nil {
		t.Fatal(err)
	}
	if !loaded.Global.TLS.Frontend.Server.RequireClientAuth {
		t.Fatal("frontend client authentication is not required")
	}
	if !loaded.Global.TLS.Internode.Server.RequireClientAuth {
		t.Fatal("internode client authentication is not required")
	}
	if len(loaded.Global.TLS.Frontend.Server.ClientCAFiles) != 1 {
		t.Fatal("frontend client CA file is not configured")
	}
	if loaded.Global.TLS.Frontend.Client.ServerName != "temporal" {
		t.Fatal("frontend TLS server name is not pinned")
	}
}
