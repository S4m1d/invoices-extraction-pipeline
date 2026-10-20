// Package migrations embeds the SQL migrations (integrator/specs/overview.md, Schema migrations).
package migrations

import "embed"

//go:embed *.sql
var FS embed.FS
