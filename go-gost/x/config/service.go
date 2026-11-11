package config

// ActiveServiceConfig removes the pause marker from a copy, leaving the stored
// configuration paused until binding and registration have succeeded.
func ActiveServiceConfig(cfg ServiceConfig) ServiceConfig {
	metadata := make(map[string]any, len(cfg.Metadata))
	for key, value := range cfg.Metadata {
		if key != "paused" {
			metadata[key] = value
		}
	}
	cfg.Metadata = metadata
	if len(metadata) == 0 {
		cfg.Metadata = nil
	}
	return cfg
}
