// Package storage writes original documents to MinIO.
package storage

import (
	"bytes"
	"context"
	"fmt"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"

	"github.com/S4m1d/invoices-extraction-pipeline/ingestor/internal/correlation"
)

// Bucket is the object storage bucket (see /specs/pipeline-contracts.md).
const Bucket = "invoices"

type Minio struct {
	client *minio.Client
	bucket string
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
	return &Minio{client: client, bucket: Bucket}, nil
}

// PutPDF stores data under key with content type application/pdf.
func (m *Minio) PutPDF(ctx context.Context, key string, data []byte) error {
	_, err := m.client.PutObject(ctx, m.bucket, key, bytes.NewReader(data), int64(len(data)),
		minio.PutObjectOptions{ContentType: "application/pdf"})
	if err != nil {
		return fmt.Errorf("put %s: %w", key, err)
	}
	return nil
}

// Check reports whether MinIO is reachable and the bucket exists.
func (m *Minio) Check(ctx context.Context) error {
	ok, err := m.client.BucketExists(ctx, m.bucket)
	if err != nil {
		return fmt.Errorf("bucket %s: %w", m.bucket, err)
	}
	if !ok {
		return fmt.Errorf("bucket %s does not exist", m.bucket)
	}
	return nil
}
