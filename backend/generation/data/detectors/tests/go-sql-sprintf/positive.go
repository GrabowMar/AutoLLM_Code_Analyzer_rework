package store

import (
	"database/sql"
	"fmt"
)

func Find(db *sql.DB, name string) (*sql.Rows, error) {
	query := fmt.Sprintf("SELECT * FROM users WHERE name = '%s'", name)
	return db.Query(query)
}
