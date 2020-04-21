""" Classes for handling SightingDB Connection """

class Connection(object):
    """ The Connection object handles the host, port, apikey and whether the
    SSL connection must be verified or not.
    """
    def __init__(self, host=None, port=9999, apikey=None, verify=False):
        """Initialize the instance with the connection parameters.

        Arguments:

        * host -- host where SightingDB is listening
        * port -- port where SightingDB is listening
        * apikey -- API key to be used to authenticating every action
        * verify -- Whether the SSL connection has valid certificates used or not
        """
        self.host = host
        self.port = port
        self.apikey = apikey
        self.verify = verify
        self.warnings_disabled = False

    def disable_ssl_warnings(self):
        """If the SSL verification is set to False, warnings will appear for each
        request. This mode removes those warnings.
        """
        self.warnings_disabled = True
        
    def get_host(self):
        """Return the connection host.
        """
        return self.host
    
    def get_port(self):
        """Return the connection port.
        """
        return self.port
    
    def get_apikey(self):
        """Return the connection api key.
        """
        return self.apikey
    
