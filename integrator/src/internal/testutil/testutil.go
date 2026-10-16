//go:build integration

// Package testutil provides integration-test helpers: a temporary PostgreSQL database, a TCP proxy
// that can simulate a database outage, and a temporary RabbitMQ vhost.
//
// Environment: DATABASE_URL (integrator credentials; the database name is replaced),
// POSTGRES_ADMIN_URL (a superuser, to create and drop databases), AMQP_URL, RABBITMQ_MANAGEMENT_URL.
package testutil

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	amqp "github.com/rabbitmq/amqp091-go"
)

func Env(t *testing.T, name string) string {
	t.Helper()
	v := os.Getenv(name)
	if v == "" {
		t.Fatalf("%s is not set", name)
	}
	return v
}

// TempDatabase creates a database owned by the DATABASE_URL user and drops it after the test.
// It returns a connection URL for that user and database, going through the given address if not empty.
func TempDatabase(t *testing.T, via string) string {
	t.Helper()
	ctx := context.Background()
	user, err := url.Parse(Env(t, "DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	name := "it_" + uuid.NewString()[:8]
	admin, err := pgx.Connect(ctx, Env(t, "POSTGRES_ADMIN_URL"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := admin.Exec(ctx, fmt.Sprintf("CREATE DATABASE %s OWNER %s", name, user.User.Username())); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		admin.Exec(ctx, fmt.Sprintf("DROP DATABASE %s WITH (FORCE)", name))
		admin.Close(ctx)
	})
	u := *user
	u.Path = "/" + name
	if via != "" {
		u.Host = via
	}
	return u.String()
}

// AdminQuery runs a query as the superuser in the given database URL's database.
func AdminConn(t *testing.T, dbURL string) *pgx.Conn {
	t.Helper()
	admin, _ := url.Parse(Env(t, "POSTGRES_ADMIN_URL"))
	target, _ := url.Parse(dbURL)
	admin.Path = target.Path
	conn, err := pgx.Connect(context.Background(), admin.String())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close(context.Background()) })
	return conn
}

// Proxy forwards TCP connections to target until Down is called; then it refuses new connections
// and closes existing ones, as a database outage would. Up starts forwarding again.
type Proxy struct {
	Addr   string
	target string
	mu     sync.Mutex
	ln     net.Listener
	conns  map[net.Conn]bool
}

func NewProxy(t *testing.T, target string) *Proxy {
	t.Helper()
	p := &Proxy{target: target, conns: map[net.Conn]bool{}}
	p.Up(t, "127.0.0.1:0")
	t.Cleanup(func() { p.Down() })
	return p
}

func (p *Proxy) Up(t *testing.T, addr string) {
	t.Helper()
	if addr == "" {
		addr = p.Addr
	}
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	p.mu.Lock()
	p.ln, p.Addr = ln, ln.Addr().String()
	p.mu.Unlock()
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			go p.forward(c)
		}
	}()
}

func (p *Proxy) forward(c net.Conn) {
	s, err := net.Dial("tcp", p.target)
	if err != nil {
		c.Close()
		return
	}
	p.mu.Lock()
	p.conns[c], p.conns[s] = true, true
	p.mu.Unlock()
	go func() { io.Copy(s, c); s.Close() }()
	io.Copy(c, s)
	c.Close()
}

func (p *Proxy) Down() {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.ln != nil {
		p.ln.Close()
		p.ln = nil
	}
	for c := range p.conns {
		c.Close()
	}
	p.conns = map[net.Conn]bool{}
}

// PostgresTarget is host:port of DATABASE_URL.
func PostgresTarget(t *testing.T) string {
	u, err := url.Parse(Env(t, "DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	return u.Host
}

// Vhost creates a RabbitMQ vhost with the contract's queues, dead-letter exchange and policy,
// and deletes it after the test. It returns the AMQP URL for it and a management helper.
func Vhost(t *testing.T) (string, *Management) {
	t.Helper()
	m := &Management{base: Env(t, "RABBITMQ_MANAGEMENT_URL")}
	name := "test-integrator-" + uuid.NewString()[:8]
	q := url.PathEscape(name)
	m.Call(t, "PUT", "/api/vhosts/"+q, map[string]any{})
	mu, _ := url.Parse(m.base)
	m.Call(t, "PUT", "/api/permissions/"+q+"/"+mu.User.Username(), map[string]string{"configure": ".*", "write": ".*", "read": ".*"})
	m.Call(t, "PUT", "/api/policies/"+q+"/dead-letter", map[string]any{
		"pattern": `^documents\.(ingested|text_extracted|fields_extracted)$`, "apply-to": "queues",
		"definition": map[string]any{"dead-letter-exchange": "dlx"}})
	t.Cleanup(func() { m.Call(t, "DELETE", "/api/vhosts/"+q, nil) })
	m.Vhost = name

	au, _ := url.Parse(Env(t, "AMQP_URL"))
	au.Path = "/" + name
	amqpURL := au.String()
	conn, err := amqp.Dial(amqpURL)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	ch, _ := conn.Channel()
	ch.ExchangeDeclare("dlx", "direct", true, false, false, false, nil)
	for _, qn := range []string{"documents.ingested", "documents.text_extracted", "documents.fields_extracted", "documents.failed"} {
		if _, err := ch.QueueDeclare(qn, true, false, false, false, nil); err != nil {
			t.Fatal(err)
		}
	}
	for _, qn := range []string{"documents.ingested", "documents.text_extracted", "documents.fields_extracted"} {
		ch.QueueDeclare(qn+".dlq", true, false, false, false, nil)
		ch.QueueBind(qn+".dlq", qn, "dlx", false, nil)
	}
	return amqpURL, m
}

type Management struct {
	base  string
	Vhost string
}

func (m *Management) Call(t *testing.T, method, path string, body any) []byte {
	t.Helper()
	u, _ := url.Parse(m.base)
	var rd io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		rd = bytes.NewReader(b)
	}
	req, _ := http.NewRequest(method, u.Scheme+"://"+u.Host+path, rd)
	pw, _ := u.User.Password()
	req.SetBasicAuth(u.User.Username(), pw)
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	out, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 300 && !(method == "DELETE" && resp.StatusCode == 404) {
		t.Fatalf("%s %s: %d %s", method, path, resp.StatusCode, out)
	}
	return out
}

// CloseConnections closes all AMQP connections of the vhost (simulates a broker connection loss).
// The management API lists new connections with a delay, so it waits up to 10 s for them.
func (m *Management) CloseConnections(t *testing.T) int {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		var conns []map[string]any
		json.Unmarshal(m.Call(t, "GET", "/api/connections", nil), &conns)
		n := 0
		for _, c := range conns {
			if c["vhost"] == m.Vhost {
				m.Call(t, "DELETE", "/api/connections/"+url.PathEscape(c["name"].(string)), nil)
				n++
			}
		}
		if n > 0 {
			return n
		}
		time.Sleep(200 * time.Millisecond)
	}
	return 0
}
