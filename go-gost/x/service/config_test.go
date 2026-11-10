package service

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadConfigAppliesProtocolDetectionBeforeServing(t *testing.T) {
	previousTLS, previousHTTP, previousSOCKS, previousWrap := isTls, isHttp, isSocks, needWrap
	t.Cleanup(func() {
		isTls, isHttp, isSocks, needWrap = previousTLS, previousHTTP, previousSOCKS, previousWrap
	})
	path := filepath.Join(t.TempDir(), "config.json")
	for _, tc := range []struct {
		name             string
		json             string
		tls, http, socks int
		wrap             bool
	}{
		{"http", `{"http":1}`, 0, 1, 0, true},
		{"tls", `{"tls":1}`, 1, 0, 0, true},
		{"socks", `{"socks":1}`, 0, 0, 1, true},
		{"disabled", `{}`, 0, 0, 0, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if err := os.WriteFile(path, []byte(tc.json), 0600); err != nil {
				t.Fatal(err)
			}
			if _, err := LoadConfig(path); err != nil {
				t.Fatal(err)
			}
			if isTls != tc.tls || isHttp != tc.http || isSocks != tc.socks || needWrap != tc.wrap {
				t.Fatalf("protocol detection state = (%d, %d, %d, %t), want (%d, %d, %d, %t)",
					isTls, isHttp, isSocks, needWrap, tc.tls, tc.http, tc.socks, tc.wrap)
			}
		})
	}
}
