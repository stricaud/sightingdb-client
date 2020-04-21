class Connection(object):
    def __init__(self, host=None, port=9999, apikey=None, verify=False):
        self.host = host
        self.port = port
        self.apikey = apikey
        self.verify = verify
        self.warnings_disabled = False

    def disable_ssl_warnings(self):
        self.warnings_disabled = True
        
    def get_host(self):
        return self.host
    
    def get_port(self):
        return self.port
    
    def get_apikey(self):
        return self.apikey
    
