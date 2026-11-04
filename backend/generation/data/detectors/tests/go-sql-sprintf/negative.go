package store

import (
	"database/sql"
	"fmt"
)

func Find(db *sql.DB, name string) (*sql.Rows, error) {
	fmt.Println(fmt.Sprintf("looking up %s", name))
	return db.Query("SELECT * FROM users WHERE name = $1", name)
}
