// Package storage reads extracted.json from MinIO. Adapted from ingestor.
package storage

import (
	"context"
	"errors"
	"fmt"
	"io"
	"time"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"

	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/correlation"
)

// Bucket is the object storage bucket (see /specs/pipeline-contracts.md).
const Bucket = "invoices"

const callTimeout = 10 * time.Second

var (
	ErrNotFound    = errors.New("object not found")
	ErrUnavailable = errors.New("storage unavailable")
)

// Object is an object's content and its user metadata (keys lower-case, without x-amz-meta-).
type Object struct {
	Data     []byte
	Metadata map[string]string
}

type Minio struct {
	client *minio.Client
}

func NewMinio(endpoint, accessKey, secretKey string, useSSL bool) (*Minio, error) {
	base, err := minio.DefaultTransport(useSSL)
	if err != nil {
		return nil, fmt.Errorf("minio transport: %w", err)
	}
	client, err := minio.New(endpoint, &minio.Options{
		Creds:     credentials.NewStaticV4(accessKey, secretKey, ""),
		Secure:    useSSL,
		Transport: &correlation.Transport{Base: base},
	})
	if err != nil {
		return nil, fmt.Errorf("minio client: %w", err)
	}
	return &Minio{client: client}, nil
}

// Get reads an object. Errors wrap ErrNotFound or ErrUnavailable.
func (m *Minio) Get(ctx context.Context, key string) (Object, error) {
	ctx, cancel := context.WithTimeout(ctx, callTimeout)
	defer cancel()
	obj, err := m.client.GetObject(ctx, Bucket, key, minio.GetObjectOptions{})
	if err != nil {
		return Object{}, classify(key, err)
	}
	defer obj.Close()
	data, err := io.ReadAll(obj)
	if err != nil {
		return Object{}, classify(key, err)
	}
	info, err := obj.Stat()
	if err != nil {
		return Object{}, classify(key, err)
	}
	meta := map[string]string{}
	for k, v := range info.UserMetadata {
		meta[normalizeKey(k)] = v
	}
	return Object{Data: data, Metadata: meta}, nil
}

// Check reports whether MinIO is reachable and the bucket exists.
func (m *Minio) Check(ctx context.Context) error {
	ok, err := m.client.BucketExists(ctx, Bucket)
	if err != nil {
		return fmt.Errorf("bucket %s: %w", Bucket, err)
	}
	if !ok {
		return fmt.Errorf("bucket %s does not exist", Bucket)
	}
	return nil
}

func classify(key string, err error) error {
	if minio.ToErrorResponse(err).Code == "NoSuchKey" {
		return fmt.Errorf("%w: %s/%s", ErrNotFound, Bucket, key)
	}
	return fmt.Errorf("%w: get %s/%s: %v", ErrUnavailable, Bucket, key, err)
}

func normalizeKey(k string) string {
	b := []byte(k)
	for i, c := range b {
		if 'A' <= c && c <= 'Z' {
			b[i] = c + 'a' - 'A'
		}
	}
	s := string(b)
	const prefix = "x-amz-meta-"
	if len(s) > len(prefix) && s[:len(prefix)] == prefix {
		return s[len(prefix):]
	}
	return s
}
