import requests
import json

class SDBDelete:
    def __init__(self, connection):
        self.con = connection

        if self.con.warnings_disabled:
            from requests.packages.urllib3.exceptions import InsecureRequestWarning
            requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

        
    def delete(self, namespace):
        r = requests.post("https://%s:%d/d/%s" % (self.con.host, self.con.port, namespace), headers={"content-type": "application/json","Authorization": self.con.apikey},
                          verify=self.con.verify)
        if r.status_code == 200:
            return True
        return False
    
