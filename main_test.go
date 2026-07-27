package main

import (
	"encoding/json"
	"math"
	"testing"
)

func TestEncodeDecodeRoundTrip(t *testing.T) {
	original := make([]float64, EmbedDim)
	for i := range original {
		original[i] = float64(i) * 0.01
	}

	blob := encodeEmbedding(original)
	decoded, ok := decodeEmbedding(blob)
	if !ok {
		t.Fatal("decodeEmbedding returned ok=false")
	}
	if len(decoded) != EmbedDim {
		t.Fatalf("expected len %d, got %d", EmbedDim, len(decoded))
	}
	for i := range original {
		if math.Abs(original[i]-decoded[i]) > 1e-6 {
			t.Errorf("index %d: expected %f, got %f", i, original[i], decoded[i])
		}
	}
}

func TestEncodeEmbeddingSize(t *testing.T) {
	v := make([]float64, EmbedDim)
	blob := encodeEmbedding(v)
	expected := EmbedDim * 4
	if len(blob) != expected {
		t.Errorf("expected %d bytes, got %d", expected, len(blob))
	}
}

func TestDecodeEmbeddingLegacyJSON(t *testing.T) {
	original := []float64{0.1, 0.2, 0.3, 0.4, 0.5}
	data, err := json.Marshal(original)
	if err != nil {
		t.Fatal(err)
	}

	decoded, ok := decodeEmbedding(data)
	if !ok {
		t.Fatal("decodeEmbedding returned ok=false for JSON input")
	}
	if len(decoded) != len(original) {
		t.Fatalf("expected len %d, got %d", len(original), len(decoded))
	}
	for i := range original {
		if math.Abs(original[i]-decoded[i]) > 1e-10 {
			t.Errorf("index %d: expected %f, got %f", i, original[i], decoded[i])
		}
	}
}

func TestDecodeEmbeddingEmpty(t *testing.T) {
	_, ok := decodeEmbedding([]byte{})
	if ok {
		t.Error("expected ok=false for empty input")
	}
}

func TestDecodeEmbeddingNil(t *testing.T) {
	_, ok := decodeEmbedding(nil)
	if ok {
		t.Error("expected ok=false for nil input")
	}
}

func TestDecodeEmbeddingOddLength(t *testing.T) {
	_, ok := decodeEmbedding([]byte{0x01, 0x02, 0x03})
	if ok {
		t.Error("expected ok=false for odd-length input")
	}
}

func TestCosineSimilarityIdentical(t *testing.T) {
	a := []float64{1.0, 2.0, 3.0}
	score := cosineSimilarity(a, a)
	if math.Abs(score-1.0) > 1e-10 {
		t.Errorf("expected 1.0, got %f", score)
	}
}

func TestCosineSimilarityOrthogonal(t *testing.T) {
	a := []float64{1.0, 0.0}
	b := []float64{0.0, 1.0}
	score := cosineSimilarity(a, b)
	if math.Abs(score) > 1e-10 {
		t.Errorf("expected 0.0, got %f", score)
	}
}

func TestCosineSimilarityOpposite(t *testing.T) {
	a := []float64{1.0, 2.0, 3.0}
	b := []float64{-1.0, -2.0, -3.0}
	score := cosineSimilarity(a, b)
	if math.Abs(score-(-1.0)) > 1e-10 {
		t.Errorf("expected -1.0, got %f", score)
	}
}

func TestCosineSimilarityZeroVector(t *testing.T) {
	a := []float64{0.0, 0.0, 0.0}
	b := []float64{1.0, 2.0, 3.0}
	score := cosineSimilarity(a, b)
	if score != 0.0 {
		t.Errorf("expected 0.0, got %f", score)
	}
}

func TestCosineSimilarityKnownValue(t *testing.T) {
	a := []float64{1.0, 0.0}
	b := []float64{1.0, 1.0}
	// cos(45°) = 1/sqrt(2) ≈ 0.7071
	expected := 1.0 / math.Sqrt(2.0)
	score := cosineSimilarity(a, b)
	if math.Abs(score-expected) > 1e-10 {
		t.Errorf("expected %f, got %f", expected, score)
	}
}

func TestGetFriendlyKeybindTextSuperOnly(t *testing.T) {
	result := getFriendlyKeybindText(64, "q")
	if result != "SUPER + Q" {
		t.Errorf("expected 'SUPER + Q', got '%s'", result)
	}
}

func TestGetFriendlyKeybindTextSuperCtrl(t *testing.T) {
	result := getFriendlyKeybindText(64|4, "t")
	if result != "SUPER + CTRL + T" {
		t.Errorf("expected 'SUPER + CTRL + T', got '%s'", result)
	}
}

func TestGetFriendlyKeybindTextAllModifiers(t *testing.T) {
	result := getFriendlyKeybindText(64|4|8|1, "a")
	if result != "SUPER + CTRL + ALT + SHIFT + A" {
		t.Errorf("expected 'SUPER + CTRL + ALT + SHIFT + A', got '%s'", result)
	}
}

func TestGetFriendlyKeybindTextNoModifiers(t *testing.T) {
	result := getFriendlyKeybindText(0, "f1")
	if result != "F1" {
		t.Errorf("expected 'F1', got '%s'", result)
	}
}

func TestTranslateKeycode(t *testing.T) {
	tests := []struct {
		code     uint16
		expected string
	}{
		{1, "escape"},
		{28, "enter"},
		{57, "space"},
		{16, "q"},
		{30, "a"},
		{44, "z"},
		{14, "backspace"},
		{15, "tab"},
		{103, "up"},
		{108, "down"},
		{105, "left"},
		{106, "right"},
		{59, "f1"},
		{88, "f12"},
		{0, ""},
		{999, ""},
	}
	for _, tt := range tests {
		got := translateKeycode(tt.code)
		if got != tt.expected {
			t.Errorf("translateKeycode(%d): expected %q, got %q", tt.code, tt.expected, got)
		}
	}
}
