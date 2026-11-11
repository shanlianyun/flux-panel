package api

import (
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/go-gost/x/config"
	"github.com/go-gost/x/config/loader"
	_ "github.com/go-gost/x/connector/direct"
	_ "github.com/go-gost/x/connector/http"
	_ "github.com/go-gost/x/dialer/direct"
	_ "github.com/go-gost/x/dialer/tcp"
	_ "github.com/go-gost/x/handler/forward/local"
	_ "github.com/go-gost/x/listener/tcp"
	"github.com/go-gost/x/registry"
)

func pausedAPIFixture(t *testing.T) (*gin.Engine, *config.ServiceConfig) {
	t.Helper()
	config.Set(&config.Config{})
	if err := loader.Load(config.Global()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		for name, svc := range registry.ServiceRegistry().GetAll() {
			svc.Close()
			registry.ServiceRegistry().Unregister(name)
		}
		config.Set(&config.Config{})
	})
	reservation, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := reservation.Addr().String()
	reservation.Close()
	cfg := &config.ServiceConfig{Name: "recovery_tcp", Addr: address,
		Listener: &config.ListenerConfig{Type: "tcp"}, Handler: &config.HandlerConfig{Type: "tcp"},
		Metadata: map[string]any{"paused": true}}
	config.Set(&config.Config{Services: []*config.ServiceConfig{cfg}})
	if err := loader.Load(config.Global()); err != nil {
		t.Fatal(err)
	}
	gin.SetMode(gin.TestMode)
	router := gin.New()
	Register(router, nil)
	return router, cfg
}

func resumeAPI(router http.Handler, batch bool) *httptest.ResponseRecorder {
	path, body := "/config/services/recovery_tcp/resume", ""
	if batch {
		path, body = "/config/services/batch/resume", `{"services":["recovery_tcp"]}`
	}
	return callResumeAPI(router, path, body)
}

func callResumeAPI(router http.Handler, path, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.SetBasicAuth("test", "test")
	recorder := httptest.NewRecorder()
	router.ServeHTTP(recorder, req)
	return recorder
}

func TestHTTPResumeRefusesMissingDependencies(t *testing.T) {
	for _, batch := range []bool{false, true} {
		for _, dependency := range []string{"chain", "limiter"} {
			t.Run(fmt.Sprintf("%s/batch=%t", dependency, batch), func(t *testing.T) {
				router, cfg := pausedAPIFixture(t)
				if dependency == "chain" {
					cfg.Handler.Chain = "missing-exit"
				} else {
					cfg.Limiter = "missing-speed"
				}
				if response := resumeAPI(router, batch); response.Code == http.StatusOK {
					t.Fatal("resume accepted missing " + dependency)
				}
				if !registry.ServiceRegistry().IsRegistered(cfg.Name) || cfg.Metadata["paused"] != true {
					t.Fatal("dependency failure lost resumability")
				}
			})
		}
	}
}

func TestHTTPBatchFailureRollsBackEarlierListenerAndCanRetry(t *testing.T) {
	router, cfg := pausedAPIFixture(t)
	busy, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer busy.Close()
	second := *cfg
	second.Name, second.Addr = "second_tcp", busy.Addr().String()
	config.Set(&config.Config{Services: []*config.ServiceConfig{cfg, &second}})
	if err := loader.Load(config.Global()); err != nil {
		t.Fatal(err)
	}
	body := `{"services":["recovery_tcp","second_tcp"]}`
	if response := callResumeAPI(router, "/config/services/batch/resume", body); response.Code == http.StatusOK {
		t.Fatal("batch resume accepted occupied second port")
	}
	if c, err := net.DialTimeout("tcp", cfg.Addr, time.Second); err == nil {
		c.Close()
		t.Fatal("failed batch left earlier listener running")
	}
	for _, stored := range config.Global().Services {
		if !registry.ServiceRegistry().IsRegistered(stored.Name) || stored.Metadata["paused"] != true {
			t.Fatal("failed batch lost paused configuration or registry entry")
		}
	}
	busy.Close()
	if response := callResumeAPI(router, "/config/services/batch/resume", body); response.Code != http.StatusOK {
		t.Fatalf("retry: %d %s", response.Code, response.Body.String())
	}
}

func TestHTTPResumeOpensPersistedPausedListener(t *testing.T) {
	for _, batch := range []bool{false, true} {
		name := "single"
		if batch {
			name = "batch"
		}
		t.Run(name, func(t *testing.T) {
			router, cfg := pausedAPIFixture(t)
			response := resumeAPI(router, batch)
			if response.Code != http.StatusOK {
				t.Fatalf("resume: %d %s", response.Code, response.Body.String())
			}
			c, err := net.DialTimeout("tcp", cfg.Addr, time.Second)
			if err != nil {
				t.Fatalf("successful resume did not bind listener: %v", err)
			}
			c.Close()
			if cfg.Metadata["paused"] == true {
				t.Fatal("resume retained paused flag")
			}
		})
	}
}

func TestHTTPFailedResumeKeepsPausedRuleAvailableForRetry(t *testing.T) {
	for _, batch := range []bool{false, true} {
		name := "single"
		if batch {
			name = "batch"
		}
		t.Run(name, func(t *testing.T) {
			router, cfg := pausedAPIFixture(t)
			busy, err := net.Listen("tcp", cfg.Addr)
			if err != nil {
				t.Fatal(err)
			}
			defer busy.Close()
			if response := resumeAPI(router, batch); response.Code == http.StatusOK {
				t.Fatal("resume accepted occupied port")
			}
			if !registry.ServiceRegistry().IsRegistered(cfg.Name) || cfg.Metadata["paused"] != true {
				t.Fatal("failed resume lost paused registry entry or configuration")
			}
			busy.Close()
			if response := resumeAPI(router, batch); response.Code != http.StatusOK {
				t.Fatalf("retry: %d %s", response.Code, response.Body.String())
			}
		})
	}
}
