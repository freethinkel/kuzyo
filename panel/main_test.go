package main

import (
	"bytes"
	"testing"
)

func TestHelpers(t *testing.T) {
	// 2 of 4 cores' worth of the host's CPU time: 200% of one core.
	if got := cpuPercent(sample{100, 1000}, sample{300, 1400}, 4); got != 200 {
		t.Errorf("cpuPercent = %v, want 200", got)
	}
	if got := cpuPercent(sample{100, 1000}, sample{300, 1000}, 4); got != 0 {
		t.Errorf("cpuPercent with no time passed = %v, want 0", got)
	}

	frames := []byte{1, 0, 0, 0, 0, 0, 0, 4, 'o', 'u', 't', '\n', 2, 0, 0, 0, 0, 0, 0, 4, 'e', 'r', 'r', '\n'}
	var out bytes.Buffer
	if err := demux(&out, bytes.NewReader(frames)); err != nil || out.String() != "out\nerr\n" {
		t.Errorf("demux = %q, %v", out.String(), err)
	}

	if got := spark([]float64{0, 5, 10, 20}); got != "▁▃▅█" {
		t.Errorf("spark = %q", got)
	}
}
