import requests
import json

class SDBAuth:
    def __init__(self, connection):
        self.con = connection

    def update_apikey(self, new_apikey):
        r = requests.get("https://%s:%d/w/_config/acl/apikeys/%s?val=\"\"" % (self.con.host, self.con.port, new_apikey),
                         headers={"content-type": "application/json","Authorization": self.con.apikey},
                         verify=self.con.verify)
        print(r.text)
        r = requests.get("https://%s:%d/d/_config/acl/apikeys/%s" % (self.con.host, self.con.port, self.con.apikey),
                         headers={"content-type": "application/json","Authorization": new_apikey},
                        verify=self.con.verify)
        print(r.text)
        return r.status_code

        


