package api

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/go-gost/x/config"
)

func TestManagementAPIRequiresConfiguredAuthenticator(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	Register(router, nil)
	req := httptest.NewRequest(http.MethodGet, "/config", nil)
	req.SetBasicAuth("anything", "anything")
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("missing authenticator accepted request with status %d", rec.Code)
	}
}

func TestManagementAPIRejectsLifecycleShellHooks(t *testing.T) {
	gin.SetMode(gin.TestMode)
	config.Set(&config.Config{})
	router := gin.New()
	Register(router, &Options{Auther: testAuthenticator{}})
	req := httptest.NewRequest(http.MethodPost, "/config/services", strings.NewReader(`{
      "name":"security_hook_test",
      "addr":"127.0.0.1:0",
      "listener":{"type":"tcp"},
      "handler":{"type":"tcp"},
      "metadata":{"preUp":["touch /private/tmp/flux-panel-hook-must-not-run"]}
    }`))
	req.Header.Set("Content-Type", "application/json")
	req.SetBasicAuth("test", "test")
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code == http.StatusOK {
		t.Fatal("management API accepted executable lifecycle hook")
	}
}
