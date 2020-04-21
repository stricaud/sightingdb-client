""" Classes for handling SightingDB Authentication """

import requests
import json

class SDBAuth:
    """ SightingDB Authentication handler
    This class allows to update and create a new API key.
    """
    def __init__(self, connection):
        """Initialize the instance with the connection parameters.
        
        Arguments:

        * connection -- The connection object used to connect to SightingDB
        """
        self.con = connection

    def add_new_apikey(self, new_apikey):
        """Add a new API Key
        
        Arguments:

        * new_apikey -- The new API key you want to add to be used immediately
        """
        r = requests.get("https://%s:%d/w/_config/acl/apikeys/%s?val=\"\"" % (self.con.host, self.con.port, new_apikey),
                         headers={"content-type": "application/json","Authorization": self.con.apikey},
                         verify=self.con.verify)
        return r.status_code

    def update_apikey(self, new_apikey):
        """Update API Key, it will remove the used one from the connection object and replace it
        with the updated key provided as argument.
        
        Arguments:

        * new_apikey -- The new API key you want to use. This will update your connection api key.
        """
        r = requests.get("https://%s:%d/w/_config/acl/apikeys/%s?val=\"\"" % (self.con.host, self.con.port, new_apikey),
                         headers={"content-type": "application/json","Authorization": self.con.apikey},
                         verify=self.con.verify)
        print(r.text)
        r = requests.get("https://%s:%d/d/_config/acl/apikeys/%s" % (self.con.host, self.con.port, self.con.apikey),
                         headers={"content-type": "application/json","Authorization": new_apikey},
                        verify=self.con.verify)
        print(r.text)
        self.conf.apikey = new_apikey
        
        return r.status_code

        


