#!/usr/bin/env python3
import sightingdb

con = sightingdb.connection(host="localhost",apikey="changeme")

auth = sightingdb.auth(con)
auth.update_apikey("changed")
