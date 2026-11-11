package service

import "net"

// A paused service retains its registry slot without binding a socket or running hooks.
type pausedService struct {
	address string
	status  Status
}

func NewPausedService(address string) *pausedService {
	return &pausedService{address: address, status: Status{state: StateClosed}}
}

func (s *pausedService) Serve() error    { return nil }
func (s *pausedService) Close() error    { return nil }
func (s *pausedService) Status() *Status { return &s.status }
func (s *pausedService) Addr() net.Addr  { return pausedAddress(s.address) }

type pausedAddress string

func (a pausedAddress) Network() string { return "paused" }
func (a pausedAddress) String() string  { return string(a) }
