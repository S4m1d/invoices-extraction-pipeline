// Package testdata gives tests the extraction-schema.md example, so tests and spec cannot drift apart.
package testdata

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

// Example returns the JSON example from fields-extractor/specs/extraction-schema.md.
func Example() []byte {
	_, file, _, _ := runtime.Caller(0)
	root := filepath.Join(filepath.Dir(file), "..", "..", "..", "..")
	spec, err := os.ReadFile(filepath.Join(root, "fields-extractor", "specs", "extraction-schema.md"))
	if err != nil {
		panic(err)
	}
	s := strings.SplitN(string(spec), "## Example\n\n```json\n", 2)[1]
	return []byte(strings.SplitN(s, "\n```", 2)[0])
}
