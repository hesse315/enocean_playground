# -*- encoding: utf-8 -*-
from __future__ import print_function, unicode_literals, division, absolute_import
import logging
import datetime

import threading
try:
    import queue
except ImportError:
    import Queue as queue

from collections import OrderedDict

from enocean.protocol.packet import Packet, UTETeachInPacket
from enocean.protocol.constants import PACKET, PARSE_RESULT, RETURN_CODE
from enocean.protocol.eep import EEP


class Communicator(threading.Thread):
    '''
    Communicator base-class for EnOcean.
    Not to be used directly, only serves as base class for SerialCommunicator etc.
    '''
    logger = logging.getLogger('enocean.communicators.Communicator')

    def __init__(self, callback=None, teach_in=True):
        super(Communicator, self).__init__()
        # Create an event to stop the thread
        self._stop_flag = threading.Event()
        # Input buffer
        self._buffer = []
        # Setup packet queues
        self.transmit = queue.Queue()
        self.receive = queue.Queue()
        # Set the callback method
        self.__callback = callback
        # Internal variable for the Base ID of the module.
        self._base_id = None
        self._remaining_base_id_writes = 0xFF
        self._version_info = OrderedDict()
        # Should new messages be learned automatically? Defaults to True.
        # TODO: Not sure if we should use CO_WR_LEARNMODE??
        self.teach_in = teach_in

        self.eep = EEP()

    def _get_from_send_queue(self):
        ''' Get message from send queue, if one exists '''
        try:
            packet = self.transmit.get(block=False)
            self.logger.info('Sending packet')
            self.logger.debug(packet)
            return packet
        except queue.Empty:
            pass
        return None

    def send(self, packet):
        if not isinstance(packet, Packet):
            self.logger.error('Object to send must be an instance of Packet')
            return False
        self.transmit.put(packet)
        return True

    def stop(self):
        self._stop_flag.set()

    def parse(self):
        ''' Parses messages and puts them to receive queue '''
        # Loop while we get new messages
        while True:
            status, self._buffer, packet = Packet.parse_msg(self.eep, self._buffer)
            # If message is incomplete -> break the loop
            if status == PARSE_RESULT.INCOMPLETE:
                return status

            # If message is OK, add it to receive queue or send to the callback method
            if status == PARSE_RESULT.OK and packet:
                packet.received = datetime.datetime.now()

                if isinstance(packet, UTETeachInPacket) and self.teach_in:
                    response_packet = packet.create_response_packet(self.base_id)
                    self.logger.info('Sending response to UTE teach-in.')
                    self.send(response_packet)

                if self.__callback is None:
                    self.receive.put(packet)
                else:
                    self.__callback(packet)
                self.logger.debug(packet)

    @property
    def base_id(self):
        ''' Fetches Base ID from the transmitter, if required. Otherwise returns the currently set Base ID. '''
        # If base id is already set, return it.
        if self._base_id is not None:
            return self._base_id

        data, optional = self._catch_common_response(0x08, 4, optional_length=1)
        self._base_id = data
        self._remaining_base_id_writes = optional[0]

        # # Send COMMON_COMMAND 0x08, CO_RD_IDBASE request to the module
        # self.send(Packet(self.eep, PACKET.COMMON_COMMAND, data=[0x08]))
        # # Loop over 10 times, to make sure we catch the response.
        # # Thanks to timeout, shouldn't take more than a second.
        # # Unfortunately, all other messages received during this time are ignored.
        # for i in range(0, 10):
        #     try:
        #         packet = self.receive.get(block=True, timeout=0.1)
        #         # We're only interested in responses to the request in question.
        #         if packet.packet_type == PACKET.RESPONSE and packet.response == RETURN_CODE.OK and len(packet.response_data) == 4:  # noqa: E501
        #             # Base ID is set in the response data.
        #             self._base_id = packet.response_data
        #             self._remaining_base_id_writes = packet.optional[0]
        #             # Put packet back to the Queue, so the user can also react to it if required...
        #             self.receive.put(packet)
        #             break
        #         # Put other packets back to the Queue.
        #         self.receive.put(packet)
        #     except queue.Empty:
        #         continue
        # Return the current Base ID (might be None).
        return self._base_id

    @property
    def remaining_base_id_writes(self):
        return self._remaining_base_id_writes
    
    @base_id.setter
    def base_id(self, base_id):
        ''' Sets the Base ID manually, only for testing purposes. '''
        self._base_id = base_id

    @property
    def version_info(self):
        ''' Fetches version info from the transmitter '''
        if self._version_info:
            return self._info
        
        data, _ = self._catch_common_response(0x03, 32)
        if data:
            self._verison_info = OrderedDict()
            self._version_info['app_version'] = '.'.join(str(x) for x in data[0:4])
            self._version_info['api_version'] = '.'.join(str(x) for x in data[4:8])
            self._version_info['chip_id'] = data[8:12]
            self._version_info['chip_version']= '.'.join(str(x) for x in data[12:16])
            self._version_info['app_description'] = ''.join([chr(x) for x in data[16:32] if x!=0])

        return self._version_info

    def _catch_common_response(self, command_code, response_length, optional_length=0):
        '''send common command and return response'''
        response_data = None
        optional_data = None

        # Send COMMON_COMMAND 0x03, CO_RD_IDBASE request to the module
        self.send(Packet(self.eep, PACKET.COMMON_COMMAND, data=[command_code]))

        # Loop over 10 times, to make sure we catch the response.
        # Thanks to timeout, shouldn't take more than a second.
        # Unfortunately, all other messages received during this time are ignored.
        for i in range(0, 20):
            try:
                packet = self.receive.get(block=True, timeout=0.1)
                # We're only interested in responses to the request in question.
                if packet.packet_type == PACKET.RESPONSE and packet.response == RETURN_CODE.OK and len(packet.response_data) == response_length:  # noqa: E501
                    if not optional_length or len(packet.optional) == optional_length:
                        # Base ID is set in the response data.
                        response_data = packet.response_data
                        optional_data = packet.optional
                        # Put packet back to the Queue, so the user can also react to it if required...
                        # self.receive.put(packet)
                        break
                # Put other packets back to the Queue.
                self.receive.put(packet)
            except queue.Empty:
                continue
        # Return the current Base ID (might be None).
        return response_data, optional_data
