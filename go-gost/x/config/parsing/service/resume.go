package service

import (
	"fmt"

	"github.com/go-gost/core/service"
	"github.com/go-gost/x/config"
	"github.com/go-gost/x/registry"
)

// ParseResumedService fails closed if a retained rule lost its dependencies.
// Missing chain wrappers otherwise silently fall back to direct routing.
func ParseResumedService(cfg *config.ServiceConfig) (service.Service, error) {
	var chains []string
	if cfg.Handler != nil {
		chains = append(chains, cfg.Handler.Chain)
	}
	if cfg.Listener != nil {
		chains = append(chains, cfg.Listener.Chain)
	}
	for _, name := range chains {
		if name != "" && !registry.ChainRegistry().IsRegistered(name) {
			return nil, fmt.Errorf("chain %s not found", name)
		}
	}
	if cfg.Limiter != "" && !registry.TrafficLimiterRegistry().IsRegistered(cfg.Limiter) {
		return nil, fmt.Errorf("limiter %s not found", cfg.Limiter)
	}
	active := config.ActiveServiceConfig(*cfg)
	return ParseService(&active)
}
