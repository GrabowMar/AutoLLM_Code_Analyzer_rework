#!/bin/sh
# Copies the inventory database to the backup directory given as $1.
cd $1
cp $DATABASE_PATH backup/
