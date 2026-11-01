package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
)

type testPKI struct {
	dir        string
	serverTLS  *tls.Config
	caPath     string
	clientCert string
	clientKey  string
}

func newTestPKI(t *testing.T) testPKI {
	t.Helper()
	dir := t.TempDir()
	caKey, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	caTemplate := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "motionly-temporal-ca"},
		NotBefore:             time.Now().Add(-time.Minute),
		NotAfter:              time.Now().Add(time.Hour),
		IsCA:                  true,
		BasicConstraintsValid: true,
		KeyUsage:              x509.KeyUsageCertSign,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, caTemplate, caTemplate, &caKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	ca, _ := x509.ParseCertificate(caDER)
	issue := func(name string, serial int64, usage x509.ExtKeyUsage) (certPEM, keyPEM []byte) {
		key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
		template := &x509.Certificate{
			SerialNumber: big.NewInt(serial),
			Subject:      pkix.Name{CommonName: name},
			DNSNames:     []string{name},
			NotBefore:    time.Now().Add(-time.Minute),
			NotAfter:     time.Now().Add(time.Hour),
			KeyUsage:     x509.KeyUsageDigitalSignature,
			ExtKeyUsage:  []x509.ExtKeyUsage{usage},
		}
		der, err := x509.CreateCertificate(rand.Reader, template, ca, &key.PublicKey, caKey)
		if err != nil {
			t.Fatal(err)
		}
		keyDER, _ := x509.MarshalPKCS8PrivateKey(key)
		return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}),
			pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER})
	}
	write := func(name string, data []byte) string {
		path := filepath.Join(dir, name)
		if err := os.WriteFile(path, data, 0o600); err != nil {
			t.Fatal(err)
		}
		return path
	}
	serverCert, serverKey := issue("temporal", 2, x509.ExtKeyUsageServerAuth)
	clientCert, clientKey := issue("motionly-diagnostics", 3, x509.ExtKeyUsageClientAuth)
	pair, err := tls.X509KeyPair(serverCert, serverKey)
	if err != nil {
		t.Fatal(err)
	}
	roots := x509.NewCertPool()
	roots.AddCert(ca)
	return testPKI{
		dir: dir,
		serverTLS: &tls.Config{
			MinVersion:   tls.VersionTLS12,
			Certificates: []tls.Certificate{pair},
			ClientCAs:    roots,
			ClientAuth:   tls.RequireAndVerifyClientCert,
		},
		caPath:     write("ca.crt", pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER})),
		clientCert: write("client.crt", clientCert),
		clientKey:  write("client.key", clientKey),
	}
}

// startFrontend serves only the gRPC health service over mTLS, reporting the
// workflow service with the given status, as the Temporal frontend does.
func startFrontend(t *testing.T, pki testPKI, status healthpb.HealthCheckResponse_ServingStatus) string {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	server := grpc.NewServer(grpc.Creds(credentials.NewTLS(pki.serverTLS)))
	healthServer := health.NewServer()
	healthServer.SetServingStatus(workflowService, status)
	healthpb.RegisterHealthServer(server, healthServer)
	go func() { _ = server.Serve(listener) }()
	t.Cleanup(server.Stop)
	return listener.Addr().String()
}

func healthArgs(pki testPKI, address string, extra ...string) []string {
	args := []string{
		"health",
		"--address", address,
		"--tls-ca-path", pki.caPath,
		"--tls-cert-path", pki.clientCert,
		"--tls-key-path", pki.clientKey,
		"--timeout", "3s",
	}
	return append(args, extra...)
}

func TestHealthReportsAServingFrontendOverMTLS(t *testing.T) {
	pki := newTestPKI(t)
	address := startFrontend(t, pki, healthpb.HealthCheckResponse_SERVING)
	if err := run(healthArgs(pki, address)); err != nil {
		t.Fatalf("health check of a serving frontend failed: %v", err)
	}
}

func TestHealthFailsWhenTheFrontendIsNotServing(t *testing.T) {
	pki := newTestPKI(t)
	address := startFrontend(t, pki, healthpb.HealthCheckResponse_NOT_SERVING)
	err := run(healthArgs(pki, address))
	if err == nil || !strings.Contains(err.Error(), "NOT_SERVING") {
		t.Fatalf("expected a NOT_SERVING failure, got %v", err)
	}
}

func TestHealthVerifiesTheFrontendCertificateName(t *testing.T) {
	pki := newTestPKI(t)
	address := startFrontend(t, pki, healthpb.HealthCheckResponse_SERVING)
	if err := run(healthArgs(pki, address, "--tls-server-name", "not-temporal")); err == nil {
		t.Fatal("expected a certificate name mismatch to fail the health check")
	}
}

func TestHealthRequiresEveryTLSInput(t *testing.T) {
	err := run([]string{"health", "--tls-ca-path", "/missing"})
	if err == nil || !strings.Contains(err.Error(), "--tls-cert-path") {
		t.Fatalf("expected missing TLS flags to be refused, got %v", err)
	}
	if _, err := clientTLSConfig("/does/not/exist", "c", "k", "temporal"); err == nil {
		t.Fatal("expected an unreadable CA file to be refused")
	}
	if err := run([]string{"health", "extra"}); err == nil {
		t.Fatal("expected positional arguments to be refused")
	}
}

func TestOnlyStartAndHealthCommandsRun(t *testing.T) {
	for _, args := range [][]string{nil, {"shell"}, {"tctl"}} {
		if err := run(args); err == nil || !strings.Contains(err.Error(), "start and health") {
			t.Fatalf("run(%v) = %v, want the supported-commands error", args, err)
		}
	}
}
