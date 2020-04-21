********************************
SightingDB Python Client Library
********************************

SightingDB is a fast database to perform Sightings. It allows to count how many time someone has seen something, when was the first time, when was the last time etc.

This client library is an abstraction from the REST API.

Examples
--------

To write 3 values:

.. code-block:: python

		import sightingdb
		con = sightingdb.connection(host="localhost", apikey="changeme")
		writer = sightingdb.writer(con)
		writer.add("/key/namespace1", "pypi.org")
		writer.add("/key/namespace1", "pypi.org")
		writer.add("/key/namespace2", "example.com")
		writer.commit()

We are counting, so pypi.org will have a count of 2.

To read 2 values, it is almost like writing but ``writer`` became ``reader`` and we ``fetch`` data to iterate on:

.. code-block:: python

		import sightingdb
		con = sightingdb.connection(host="localhost", apikey="changeme")
		reader = sightingdb.reader(con)
		reader.add("/key/namespace1", "pypi.org")
		reader.add("/key/namespace2", "example.com")
		for i in reader.fetch():
		    print(str(i))

And you will be all set to read all the values.

