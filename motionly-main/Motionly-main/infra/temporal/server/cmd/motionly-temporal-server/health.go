package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"flag"
	"fmt"
	"os"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
)

// workflowService is the gRPC health service name the Temporal frontend
// reports, the same check `temporal operator cluster health` makes. Running it
// from the wrapper means the server image needs no Temporal CLI binary.
const workflowService = "temporal.api.workflowservice.v1.WorkflowService"

// checkHealth asks the frontend for its health over mTLS. The Compose health
// check presents the diagnostics certificate, which mtlsAuthorizer lets call
// only health and safe cluster-read APIs.
func checkHealth(args []string) error {
	flags := flag.NewFlagSet("health", flag.ContinueOnError)
	flags.SetOutput(os.Stderr)
	address := flags.String("address", "127.0.0.1:7233", "frontend gRPC address")
	caPath := flags.String("tls-ca-path", "", "CA certificate that signs the frontend certificate")
	certPath := flags.String("tls-cert-path", "", "client certificate")
	keyPath := flags.String("tls-key-path", "", "client private key")
	serverName := flags.String("tls-server-name", "temporal", "name the frontend certificate must carry")
	timeout := flags.Duration("timeout", 4*time.Second, "how long to wait for the answer")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return errors.New("health does not accept positional arguments")
	}
	tlsConfig, err := clientTLSConfig(*caPath, *certPath, *keyPath, *serverName)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()
	if err := probe(ctx, *address, tlsConfig); err != nil {
		return err
	}
	fmt.Println("temporal frontend: SERVING")
	return nil
}

func clientTLSConfig(caPath, certPath, keyPath, serverName string) (*tls.Config, error) {
	if caPath == "" || certPath == "" || keyPath == "" || serverName == "" {
		return nil, errors.New("health needs --tls-ca-path, --tls-cert-path, --tls-key-path and --tls-server-name")
	}
	caPEM, err := os.ReadFile(caPath)
	if err != nil {
		return nil, fmt.Errorf("read health CA certificate: %w", err)
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(caPEM) {
		return nil, errors.New("health CA file holds no PEM certificate")
	}
	pair, err := tls.LoadX509KeyPair(certPath, keyPath)
	if err != nil {
		return nil, fmt.Errorf("load health client certificate: %w", err)
	}
	return &tls.Config{
		MinVersion:   tls.VersionTLS12,
		RootCAs:      roots,
		Certificates: []tls.Certificate{pair},
		ServerName:   serverName,
	}, nil
}

func probe(ctx context.Context, address string, tlsConfig *tls.Config) error {
	conn, err := grpc.NewClient(address, grpc.WithTransportCredentials(credentials.NewTLS(tlsConfig)))
	if err != nil {
		return fmt.Errorf("connect to %s: %w", address, err)
	}
	defer conn.Close()
	response, err := healthpb.NewHealthClient(conn).Check(ctx, &healthpb.HealthCheckRequest{Service: workflowService})
	if err != nil {
		return fmt.Errorf("health check: %w", err)
	}
	if status := response.GetStatus(); status != healthpb.HealthCheckResponse_SERVING {
		return fmt.Errorf("temporal frontend is %s", status)
	}
	return nil
}
