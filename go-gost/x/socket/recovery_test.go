package socket

import (
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/go-gost/x/config"
	"github.com/go-gost/x/config/loader"
	_ "github.com/go-gost/x/connector/direct"
	_ "github.com/go-gost/x/connector/forward"
	_ "github.com/go-gost/x/connector/http"
	_ "github.com/go-gost/x/dialer/direct"
	_ "github.com/go-gost/x/dialer/tcp"
	_ "github.com/go-gost/x/handler/forward/local"
	_ "github.com/go-gost/x/listener/tcp"
	"github.com/go-gost/x/registry"
	"github.com/gorilla/websocket"
)

func recoveryFixture(t *testing.T, paused bool) config.ServiceConfig {
	t.Helper()
	for name, svc := range registry.ServiceRegistry().GetAll() {
		svc.Close()
		registry.ServiceRegistry().Unregister(name)
	}
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
	return config.ServiceConfig{Name: "recovery_tcp", Addr: address, Listener: &config.ListenerConfig{Type: "tcp"}, Handler: &config.HandlerConfig{Type: "tcp"}, Metadata: map[string]any{"paused": paused}}
}

func TestPersistedPausedServiceCanResumeWithoutLosingConfig(t *testing.T) {
	cfg := recoveryFixture(t, true)
	config.Set(&config.Config{Services: []*config.ServiceConfig{&cfg}})
	if err := loader.Load(config.Global()); err != nil {
		t.Fatal(err)
	}
	go registry.ServiceRegistry().Get(cfg.Name).Serve()
	if c, err := net.DialTimeout("tcp", cfg.Addr, time.Second); err == nil {
		c.Close()
		t.Fatal("paused service is listening")
	}
	if err := resumeServices(resumeServicesRequest{Services: []string{cfg.Name}}); err != nil {
		t.Fatal(err)
	}
	c, err := net.DialTimeout("tcp", cfg.Addr, time.Second)
	if err != nil {
		t.Fatalf("resume did not open listener: %v", err)
	}
	c.Close()
	if config.Global().Services[0].Metadata["paused"] == true {
		t.Fatal("resume left paused flag")
	}
}

func TestReplayRepairsClosedRegisteredService(t *testing.T) {
	cfg := recoveryFixture(t, false)
	if err := createServices(createServicesRequest{Data: []config.ServiceConfig{cfg}}); err != nil {
		t.Fatal(err)
	}
	old := registry.ServiceRegistry().Get(cfg.Name)
	old.Close()
	if err := createServices(createServicesRequest{Data: []config.ServiceConfig{cfg}}); err != nil {
		t.Fatalf("closed registry entry prevented recovery: %v", err)
	}
	c, err := net.DialTimeout("tcp", cfg.Addr, time.Second)
	if err != nil {
		t.Fatalf("recovery did not reopen listener: %v", err)
	}
	c.Close()
	if len(config.Global().Services) != 1 {
		t.Fatal("replay duplicated persisted service")
	}
}

func TestReplayCanActivateAPausedEntryOnlyWhenRequested(t *testing.T) {
	cfg := recoveryFixture(t, true)
	config.Set(&config.Config{Services: []*config.ServiceConfig{&cfg}})
	if err := loader.Load(config.Global()); err != nil {
		t.Fatal(err)
	}
	desired := cfg
	desired.Metadata = nil
	if err := createServices(createServicesRequest{Data: []config.ServiceConfig{desired}}); err != nil {
		t.Fatal(err)
	}
	c, err := net.DialTimeout("tcp", cfg.Addr, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	c.Close()
	if config.Global().Services[0].Metadata["paused"] == true {
		t.Fatal("replay left paused metadata")
	}
}

func TestReplayRetainsExplicitPausedConfigurationWithoutBinding(t *testing.T) {
	cfg := recoveryFixture(t, true)
	for i := 0; i < 2; i++ {
		if err := createServices(createServicesRequest{Data: []config.ServiceConfig{cfg}}); err != nil {
			t.Fatal(err)
		}
		if c, err := net.DialTimeout("tcp", cfg.Addr, time.Second); err == nil {
			c.Close()
			t.Fatal("paused compensation bound a listener")
		}
		if config.Global().Services[0].Metadata["paused"] != true {
			t.Fatal("paused compensation lost pause marker")
		}
	}
	if err := resumeServices(resumeServicesRequest{Services: []string{cfg.Name}}); err != nil {
		t.Fatal(err)
	}
	c, err := net.DialTimeout("tcp", cfg.Addr, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	c.Close()
}

func TestEditingPausedServiceRetainsItsConfigWithoutOpeningListener(t *testing.T) {
	cfg := recoveryFixture(t, true)
	if err := createServices(createServicesRequest{Data: []config.ServiceConfig{cfg}}); err != nil {
		t.Fatal(err)
	}
	cfg.Limiter = "new-speed"
	if err := updateServices(updateServicesRequest{Data: []config.ServiceConfig{cfg}}); err != nil {
		t.Fatal(err)
	}
	if c, err := net.DialTimeout("tcp", cfg.Addr, time.Second); err == nil {
		c.Close()
		t.Fatal("editing paused rule reopened its listener")
	}
	stored := config.Global().Services[0]
	if stored.Metadata["paused"] != true || stored.Limiter != "new-speed" {
		t.Fatal("paused edit lost its new configuration or pause marker")
	}
	if err := createLimiter(createLimiterRequest{Data: config.LimiterConfig{Name: "new-speed", Limits: []string{"$ 1MB 1MB"}}}); err != nil {
		t.Fatal(err)
	}
	if err := resumeServices(resumeServicesRequest{Services: []string{cfg.Name}}); err != nil {
		t.Fatal(err)
	}
}

func TestResumeRefusesMissingDependenciesInsteadOfBypassingThem(t *testing.T) {
	for _, dependency := range []string{"chain", "limiter"} {
		t.Run(dependency, func(t *testing.T) {
			cfg := recoveryFixture(t, true)
			if dependency == "chain" {
				cfg.Handler.Chain = "required-exit"
			} else {
				cfg.Limiter = "required-speed"
			}
			if err := createServices(createServicesRequest{Data: []config.ServiceConfig{cfg}}); err != nil {
				t.Fatal(err)
			}
			if err := resumeServices(resumeServicesRequest{Services: []string{cfg.Name}}); err == nil {
				t.Fatal("resume accepted missing " + dependency)
			}
			if c, err := net.DialTimeout("tcp", cfg.Addr, time.Second); err == nil {
				c.Close()
				t.Fatal("missing dependency opened a listener")
			}
			if config.Global().Services[0].Metadata["paused"] != true {
				t.Fatal("failed dependency check lost paused state")
			}
		})
	}
}

func TestResumeUsesRestoredExitChainRatherThanDirectTarget(t *testing.T) {
	cfg := recoveryFixture(t, true)
	exit, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer exit.Close()
	go func() {
		c, err := exit.Accept()
		if err == nil {
			defer c.Close()
			io.Copy(c, c)
		}
	}()
	cfg.Handler.Chain = "restored-exit"
	// There is no listener at the direct target; only the restored chain can echo.
	cfg.Forwarder = &config.ForwarderConfig{Nodes: []*config.ForwardNodeConfig{{Name: "unreachable-direct", Addr: "127.0.0.1:1"}}}
	if err := createServices(createServicesRequest{Data: []config.ServiceConfig{cfg}}); err != nil {
		t.Fatal(err)
	}
	chain := config.ChainConfig{Name: "restored-exit", Hops: []*config.HopConfig{{Name: "exit-hop", Nodes: []*config.NodeConfig{{
		Name: "exit", Addr: exit.Addr().String(), Connector: &config.ConnectorConfig{Type: "forward"}, Dialer: &config.DialerConfig{Type: "tcp"},
	}}}}}
	if err := createChain(createChainRequest{Data: chain}); err != nil {
		t.Fatal(err)
	}
	if err := resumeServices(resumeServicesRequest{Services: []string{cfg.Name}}); err != nil {
		t.Fatal(err)
	}
	c, err := net.DialTimeout("tcp", cfg.Addr, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	c.SetDeadline(time.Now().Add(3 * time.Second))
	if _, err := c.Write([]byte("exit")); err != nil {
		t.Fatal(err)
	}
	var data [4]byte
	if _, err := io.ReadFull(c, data[:]); err != nil {
		t.Fatalf("restored chain did not carry traffic: %v", err)
	}
	if string(data[:]) != "exit" {
		t.Fatal("exit echo mismatch")
	}
}

func TestFailedResumeKeepsPausedRuleAvailableForRetry(t *testing.T) {
	cfg := recoveryFixture(t, true)
	config.Set(&config.Config{Services: []*config.ServiceConfig{&cfg}})
	if err := loader.Load(config.Global()); err != nil {
		t.Fatal(err)
	}
	busy, err := net.Listen("tcp", cfg.Addr)
	if err != nil {
		t.Fatal(err)
	}
	defer busy.Close()
	if err := resumeServices(resumeServicesRequest{Services: []string{cfg.Name}}); err == nil {
		t.Fatal("resume should report occupied port")
	}
	if !registry.ServiceRegistry().IsRegistered(cfg.Name) || config.Global().Services[0].Metadata["paused"] != true {
		t.Fatal("failed resume lost paused state or registry slot")
	}
	busy.Close()
	if err := resumeServices(resumeServicesRequest{Services: []string{cfg.Name}}); err != nil {
		t.Fatal(err)
	}
}

func TestFailedBatchResumeRollsBackEarlierListenerAndCanRetry(t *testing.T) {
	cfg := recoveryFixture(t, true)
	busy, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer busy.Close()
	second := cfg
	second.Name, second.Addr = "second_tcp", busy.Addr().String()
	if err := createServices(createServicesRequest{Data: []config.ServiceConfig{cfg, second}}); err != nil {
		t.Fatal(err)
	}
	req := resumeServicesRequest{Services: []string{cfg.Name, second.Name}}
	if err := resumeServices(req); err == nil {
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
	if err := resumeServices(req); err != nil {
		t.Fatalf("retry: %v", err)
	}
}

func TestReplayDoesNotReplaceHealthyServiceOrCloseConnection(t *testing.T) {
	cfg := recoveryFixture(t, false)
	echo, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer echo.Close()
	go func() {
		c, err := echo.Accept()
		if err == nil {
			defer c.Close()
			io.Copy(c, c)
		}
	}()
	cfg.Forwarder = &config.ForwarderConfig{Nodes: []*config.ForwardNodeConfig{{Name: "echo", Addr: echo.Addr().String()}}}
	if err := createServices(createServicesRequest{Data: []config.ServiceConfig{cfg}}); err != nil {
		t.Fatal(err)
	}
	old := registry.ServiceRegistry().Get(cfg.Name)
	c, err := net.DialTimeout("tcp", cfg.Addr, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	c.SetDeadline(time.Now().Add(3 * time.Second))
	for i := 0; i < 2; i++ {
		if _, err := c.Write([]byte("ping")); err != nil {
			t.Fatal(err)
		}
		var data [4]byte
		if _, err := io.ReadFull(c, data[:]); err != nil {
			t.Fatal(err)
		}
		if string(data[:]) != "ping" {
			t.Fatal("echo mismatch")
		}
		if i == 0 {
			err := createServices(createServicesRequest{Data: []config.ServiceConfig{cfg}})
			if err != nil && !strings.Contains(err.Error(), "already exists") {
				t.Fatal(err)
			}
			if registry.ServiceRegistry().Get(cfg.Name) != old {
				t.Fatal("healthy service replaced")
			}
		}
	}
}

func TestPanelOutageLeavesHealthyForwardRunning(t *testing.T) {
	cfg := recoveryFixture(t, false)
	echo, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer echo.Close()
	go func() {
		for {
			conn, err := echo.Accept()
			if err != nil {
				return
			}
			go func() {
				defer conn.Close()
				_, _ = io.Copy(conn, conn)
			}()
		}
	}()
	cfg.Forwarder = &config.ForwarderConfig{Nodes: []*config.ForwardNodeConfig{{Name: "echo", Addr: echo.Addr().String()}}}
	if err := createServices(createServicesRequest{Data: []config.ServiceConfig{cfg}}); err != nil {
		t.Fatal(err)
	}

	var outage atomic.Bool
	var connectedOnce sync.Once
	connected := make(chan struct{})
	control := make(chan *websocket.Conn, 1)
	upgrader := websocket.Upgrader{}
	panel := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if outage.Load() {
			http.Error(w, "panel maintenance", http.StatusServiceUnavailable)
			return
		}
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		connectedOnce.Do(func() {
			control <- conn
			close(connected)
		})
		defer conn.Close()
		for {
			if _, _, err := conn.ReadMessage(); err != nil {
				return
			}
		}
	}))
	defer panel.Close()

	reporter := NewWebSocketReporter("ws"+strings.TrimPrefix(panel.URL, "http"), "panel-secret")
	reporter.reconnectTime = 20 * time.Millisecond
	reporter.pingInterval = 20 * time.Millisecond
	reporter.Start()
	defer reporter.Stop()
	select {
	case <-connected:
	case <-time.After(time.Second):
		t.Fatal("reporter did not connect to panel")
	}

	assertEcho := func(conn net.Conn) {
		t.Helper()
		_ = conn.SetDeadline(time.Now().Add(time.Second))
		if _, err := conn.Write([]byte("ping")); err != nil {
			t.Fatal(err)
		}
		var response [4]byte
		if _, err := io.ReadFull(conn, response[:]); err != nil {
			t.Fatal(err)
		}
		if string(response[:]) != "ping" {
			t.Fatalf("unexpected forward response %q", response[:])
		}
	}

	stream, err := net.DialTimeout("tcp", cfg.Addr, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer stream.Close()
	assertEcho(stream)
	outage.Store(true)
	// HTTP test-server cleanup does not close hijacked WebSockets. Close the
	// actual control socket and wait until the reporter observes the outage.
	(<-control).Close()
	deadline := time.Now().Add(2 * time.Second)
	for {
		reporter.connMutex.Lock()
		connected := reporter.connected
		reporter.connMutex.Unlock()
		if !connected {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("reporter did not observe panel outage")
		}
		time.Sleep(10 * time.Millisecond)
	}
	assertEcho(stream)
	newStream, err := net.DialTimeout("tcp", cfg.Addr, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer newStream.Close()
	assertEcho(newStream)
}
