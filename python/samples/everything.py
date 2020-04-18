#!/usr/bin/env python3
import sightingdb

con = sightingdb.connection(host="localhost",apikey="changeme")

# Write values
writer = sightingdb.writer(con)
writer.add("/foo/bar", "127.0.0.1")
writer.add("/foo/bar", "127.0.0.1")
writer.commit()

# Read values
reader = sightingdb.reader(con)
reader.add("/foo/bar", "127.0.0.1")
# No such value
reader.add("/foo/bar", "192.168.0.2")
# No such namespace
reader.add("/mouahah", "192.168.0.2")
ans = reader.read_one("foo/bar", "127.0.0.1")
print(ans)
data = reader.fetch()
for i in data:
    print(str(i))

# Delete this namespace
deln = sightingdb.delete(con)
deln.delete("/foo/bar")

