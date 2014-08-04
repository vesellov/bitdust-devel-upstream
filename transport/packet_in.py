

"""
.. module:: packet_in
.. role:: red

BitPie.NET packet_in() Automat

.. raw:: html

    <a href="packet_in.png" target="_blank">
    <img src="packet_in.png" style="max-width:100%;">
    </a>

EVENTS:
    * :red:`cancel`
    * :red:`failed`
    * :red:`register-item`
    * :red:`remote-id-cached`
    * :red:`unregister-item`
    * :red:`unserialize-failed`
    * :red:`valid-inbox-packet`
"""

import os
import time

from logs import lg

from lib import automat
from lib import bpio
from lib import tmpfile
from lib import settings
from lib import contacts

from userid import identitycache

import gate
import stats
import callback
import packet_out

#------------------------------------------------------------------------------ 

_InboxItems = {}

#------------------------------------------------------------------------------ 

def items():
    """
    """
    global _InboxItems
    return _InboxItems


def create(transfer_id):
    p = PacketIn(transfer_id)
    items()[transfer_id] = p
    # lg.out(10, 'packet_in.create  %s,  %d working items now' % (
    #     transfer_id, len(items())))
    return p


def get(transfer_id):
    return items().get(transfer_id, None)
    

class PacketIn(automat.Automat):
    """
    This class implements all the functionality of the ``packet_in()`` state machine.
    """
    fast = True
    
    def __init__(self, transfer_id):
        self.transfer_id = transfer_id
        self.time = None
        self.timeout = None
        self.proto = None
        self.host = None 
        self.sender_idurl = None
        self.filename = None
        self.size = None
        self.bytes_received = None
        self.status = None
        self.error_message = None
        automat.Automat.__init__(self, 'IN(%r)' % self.transfer_id, 'AT_STARTUP', 20)
        
    def is_timed_out(self):
        if self.time is None or self.timeout is None:
            return False
        return time.time() - self.time > self.timeout

    def init(self):
        """
        Method to initialize additional variables and flags at creation of the state machine.
        """

    def state_changed(self, oldstate, newstate):
        """
        Method to to catch the moment when automat's state were changed.
        """

    def A(self, event, arg):
        #---AT_STARTUP---
        if self.state == 'AT_STARTUP':
            if event == 'register-item' :
                self.state = 'RECEIVING'
                self.doInit(arg)
        #---RECEIVING---
        elif self.state == 'RECEIVING':
            if event == 'unregister-item' and not self.isTransferFinished(arg) :
                self.state = 'FAILED'
                self.doReportFailed(arg)
                self.doEraseInputFile(arg)
                self.doDestroyMe(arg)
            elif event == 'cancel' :
                self.doCancelItem(arg)
            elif event == 'unregister-item' and self.isTransferFinished(arg) and not self.isRemoteIdentityCached(arg) :
                self.state = 'CACHING'
                self.doCacheRemoteIdentity(arg)
            elif event == 'unregister-item' and self.isTransferFinished(arg) and self.isRemoteIdentityCached(arg) :
                self.state = 'INBOX?'
                self.doReadAndUnserialize(arg)
        #---INBOX?---
        elif self.state == 'INBOX?':
            if event == 'valid-inbox-packet' :
                self.state = 'DONE'
                self.doReportReceived(arg)
                self.doEraseInputFile(arg)
                self.doDestroyMe(arg)
            elif event == 'unserialize-failed' :
                self.state = 'FAILED'
                self.doReportFailed(arg)
                self.doEraseInputFile(arg)
                self.doDestroyMe(arg)
        #---FAILED---
        elif self.state == 'FAILED':
            pass
        #---DONE---
        elif self.state == 'DONE':
            pass
        #---CACHING---
        elif self.state == 'CACHING':
            if event == 'failed' :
                self.state = 'FAILED'
                self.doReportFailed(arg)
                self.doEraseInputFile(arg)
                self.doDestroyMe(arg)
            elif event == 'remote-id-cached' :
                self.state = 'INBOX?'
                self.doReadAndUnserialize(arg)

    def isTransferFinished(self, arg):
        """
        Condition method.
        """
        status, bytes_received, error_message = arg
        if status != 'finished':
            return False
        if self.size and self.size>0 and self.size != bytes_received:
            return False
        return True

    def isRemoteIdentityCached(self, arg):
        """
        Condition method.
        """
        return identitycache.HasKey(self.sender_idurl)

    def doInit(self, arg):
        """
        Action method.
        """
        self.proto, self.host, self.sender_idurl, self.filename, self.size = arg
        self.time = time.time()
        self.timeout = max(int(self.size/settings.SendingSpeedLimit()), 10)

    def doEraseInputFile(self, arg):
        """
        Action method.
        """
        tmpfile.throw_out(self.filename, 'received')

    def doCancelItem(self, arg):
        """
        Action method.
        """
        gate.transport(self.proto).call('cancel_file_receiving', self.transfer_id)

    def doCacheRemoteIdentity(self, arg):
        """
        Action method.
        """
        d = identitycache.immediatelyCaching(self.sender_idurl)
        d.addCallback(self._remote_identity_cached, arg)
        d.addErrback(lambda err: self.automat('failed'))

    def doReadAndUnserialize(self, arg):
        """
        Action method.
        """
        self.status, self.bytes_received, self.error_message = arg
        # DO UNSERIALIZE HERE , no exceptions
        newpacket = gate.inbox(self)
        if newpacket is None:
            lg.out(14, '<<< IN <<<  !!!NONE!!!   [%s]   %s from %s %s' % (
                         self.proto.upper().ljust(5), self.status.ljust(8), 
                         self.host, os.path.basename(self.filename),))
            # net_misc.ConnectionFailed(None, proto, 'receiveStatusReport %s' % host)
            try:
                fd, fn = tmpfile.make('other', '.inbox.error')
                data = bpio.ReadBinaryFile(self.filename)
                os.write(fd, 'from %s:%s %s\n' % (self.proto, self.host, self.status))
                os.write(fd, str(data))
                os.close(fd)
                os.remove(self.filename)
            except:
                lg.exc()
            self.automat('unserialize-failed', None)
        else:
            self.automat('valid-inbox-packet', newpacket)

    def doReportReceived(self, arg):
        """
        Action method.
        """
        newpacket = arg
        stats.count_inbox(self.sender_idurl, self.proto, self.status, self.bytes_received)
        for p in packet_out.search_by_response_packet(newpacket):
            p.automat('inbox-packet', newpacket)
        callback.run_inbox_callbacks(newpacket, self, self.status, self.error_message)

    def doReportFailed(self, arg):
        """
        Action method.
        """
        if arg is None:
            stats.count_inbox(self.sender_idurl, self.proto, 'failed', self.bytes_received)
            # callback.run_inbox_callbacks(None, self, self.status, self.error_message)
        else:
            status, bytes_received, error_message = arg
            stats.count_inbox(self.sender_idurl, self.proto, status, bytes_received)
            # callback.run_inbox_callbacks(None, self, status, error_message)

    def doDestroyMe(self, arg):
        """
        Remove all references to the state machine object to destroy it.
        """
        items().pop(self.transfer_id)
        automat.objects().pop(self.index)

    def _remote_identity_cached(self, xmlsrc, arg):
        sender_identity = contacts.getContact(self.sender_idurl)
        if sender_identity is None:
            self.automat('failed')
        else:
            self.automat('remote-id-cached', arg)
