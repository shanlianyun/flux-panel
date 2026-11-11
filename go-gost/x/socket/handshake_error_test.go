package socket

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestHandshakeErrorsExposeStatusWithoutLeakingCredentials(t *testing.T) {
	for _, status := range []int{200, 301, 400, 401, 403, 404, 502} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(status)
				fmt.Fprint(w, "private response body must not enter logs")
			}))
			defer server.Close()
			reporter := NewWebSocketReporter("ws"+strings.TrimPrefix(server.URL, "http")+"/system-info?type=1", "test-secret-only")
			defer reporter.Stop()
			err := reporter.connect()
			if err == nil || !strings.Contains(err.Error(), fmt.Sprintf("HTTP %d", status)) {
				t.Fatalf("want HTTP %d in handshake diagnostic; got %v", status, err)
			}
			if strings.Contains(err.Error(), "private response") || strings.Contains(err.Error(), "test-secret-only") {
				t.Fatal("handshake diagnostic exposed private data")
			}
		})
	}
}
