package main

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/go-gost/x/config"
	"github.com/go-gost/x/registry"
	"github.com/gorilla/websocket"
)

func TestPanelConnectionStartsOnlyAfterRuntimeConfigIsLoaded(t *testing.T) {
	ready := make(chan bool, 1)
	upgrader := websocket.Upgrader{}
	panel := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close()
		cfg := config.Global()
		ready <- len(cfg.Limiters) == 1 && cfg.Limiters[0].Name == "boot-ready" &&
			registry.TrafficLimiterRegistry().IsRegistered("boot-ready")
		for {
			if _, _, err := conn.ReadMessage(); err != nil {
				return
			}
		}
	}))
	defer panel.Close()
	prepareProgramConfig(t, panel.URL, `{"limiters":[{"name":"boot-ready","limits":["$ 1MB 1MB"]}]}`)
	p := &program{}
	if err := p.Init(nil); err != nil {
		t.Fatal(err)
	}
	if err := p.Start(); err != nil {
		t.Fatal(err)
	}
	defer p.Stop()
	select {
	case loaded := <-ready:
		if !loaded {
			t.Fatal("panel can send commands before local config and registries are ready")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("program lifecycle did not start the panel connection after loading config")
	}
}

func prepareProgramConfig(t *testing.T, panelURL, gostConfig string) {
	t.Helper()
	dir := t.TempDir()
	previousDir, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	previousCfg := cfgFile
	t.Cleanup(func() {
		cfgFile = previousCfg
		if err := os.Chdir(previousDir); err != nil {
			t.Error(err)
		}
	})
	if err := os.Chdir(dir); err != nil {
		t.Fatal(err)
	}
	cfgFile = filepath.Join(dir, "gost.json")
	if err := os.WriteFile(cfgFile, []byte(gostConfig), 0600); err != nil {
		t.Fatal(err)
	}
	panelConfig := fmt.Sprintf(`{"addr":%q,"secret":"startup-test-secret"}`, strings.TrimPrefix(panelURL, "http://"))
	if err := os.WriteFile("config.json", []byte(panelConfig), 0600); err != nil {
		t.Fatal(err)
	}
}
