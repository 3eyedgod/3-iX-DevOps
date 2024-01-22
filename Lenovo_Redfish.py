"""
Scan system component info and update STD.

This script is meant for Lenovo ThinkSystem SR630 V3 Server (model 7D73CTO1WW),
but should work for similar Redfish enabled Lenovo systems. Any differing parts
for future systems may need extra entries in the manufacturer/type translation
dictionary. Outline of script:

1. Parse input file to perform actions on all systems as a batch.
  a. Assumes all systems have IPMI login USERID / Password123
4. Gather component info (part number, part serial, manufacturer)
5. Update STD with component info.

The script expects an input file to exist in same directory as script. The
input file should contain a list of IPMI IPs and their respective iX serial
number separated by a space, and each system separated into lines. The name of
this input file must be 'KEY.txt'. Example below:

10.48.143.80 A1-102049
10.48.133.155 A1-102050

It is also expected that each system entry exists in STD before running script.

Usage: python lenovo.py

Authors:
    Juan Garcia <jgarcia@ixsystems.com>
    Jason Zhao <jzhao@ixsystems.com>
"""
from __future__ import annotations
import json
from pathlib import Path
import sys
import time

import psycopg
from psycopg.rows import dict_row
import requests


# Translate manufacturer parsed from system to STD manufacturer lower case
MANUFACTURER_DICT = {
    'ixsystems': 'ix systems',
    'lenovo': 'lenovo',
    'intel(r) corporation': 'intel',
    'samsung': 'samsung',
    'micron': 'micron',
    'micron technology': 'micron',
    'acbe': 'acbel',
    'grea': 'great wall',
    'mellanox technologies': 'mellanox technologies',
    'sk hynix': 'sk hynix korea',
    'hynix': 'hynix',
    'broadcom': 'broadcom',
    'broadcom limited': 'broadcom',
}

# Translate simple part type name to STD part type lower case
TYPE_DICT = {
    'cpu': 'cpu',
    'memory': 'memory',
    'nic': 'nic',
    'ssd': 'ssd m.2',
    'psu': 'power supply',
    'motherboard': 'motherboard',
    'password': 'unique password',
}


class StdDatabase:
    """
    Open a connection to STD database with read-write access.

    Attributes:
        _conn: psycopg connection to STD.
        _cursor: psycopg cursor to STD.
        insert_log: Dictionary of tables and associated newly created row IDs.
    """

    def __init__(self: StdDatabase):
        """
        Open a connection to STD database with read-write access.

        Raises:
            psycopg2.Error: Any error that is not a successful connection.
        """
        try:
            # DEV
            self._conn = psycopg.connect(
                host='host',
                dbname='dbname',
                user='user'
            )

            self._cursor = self._conn.cursor(row_factory=dict_row)
        except psycopg.Error as err:
            print(err)
            sys.exit()

        self.insert_log = {}

    def __enter__(self: StdDatabase) -> StdDatabase:
        """
        Return self for 'with' context.

        Returns:
            psycopg database connection to STD.
        """
        return self

    def __exit__(self: StdDatabase, exc_type, exc_value, exc_traceback):
        """Close and clean up self for 'with' context."""
        self.close()

    @property
    def connection(self: StdDatabase) -> psycopg.Connection:
        """
        Get connection.

        Returns:
            psycopg database connection to STD.
        """
        return self._conn

    @property
    def cursor(self: StdDatabase) -> psycopg.Cursor:
        """
        Get cursor.

        Returns:
            psycopg database connection to STD.
        """
        return self._cursor

    def commit(self: StdDatabase):
        """Manually commit a transaction."""
        self.connection.commit()

    def rollback(self: StdDatabase):
        """Manually commit a transaction."""
        self.connection.rollback()

    def close(self: StdDatabase, commit=False):
        """End transaction and close connection, default no commit."""
        if commit:
            self.commit()
        else:
            self.rollback()
        self.connection.close()

    def fetchall(self: StdDatabase) -> list[dict]:
        """
        Read all resulting rows.

        Returns:
            List of rows with asssociative column name keys.
        """
        return self.cursor.fetchall()

    def query(self: StdDatabase, query_str: str, params=None) -> list[dict]:
        """
        Execute query, return all rows, catch errors.

        Returns:
            List of rows with asssociative column name keys.

        Raises:
            psycopg2.Error: Any error that is not a successful query.
        """
        try:
            self.cursor.execute(query_str, params or ())
        except psycopg.Error as err:
            print(err)
            self.close()
            sys.exit(1)

        return self.fetchall()

    def get_system_id(self: StdDatabase, system_serial: str) -> int:
        """
        Get row ID of system by system serial number.

        Returns:
            Primary key ID of system.
        """
        query_str = '''
            SELECT id
            FROM production_system
            WHERE system_serial = %s
            LIMIT 1;
        '''
        result = self.query(query_str, (system_serial,))

        # Sanity check, but there should be a result!
        if len(result) == 0:
            print(f'Error: No result for system serial number '
                  f'\'{system_serial}\' found in production_system')
            self.close()
            sys.exit()

        return result[0]['id']

    def get_manufacturer_id(self: StdDatabase, manufacturer_name: str) -> int:
        """
        Get row ID of manufacturer by lower case name.

        Returns:
            Primary key ID of manufacturer.
        """
        query_str = '''
            SELECT id
            FROM production_manufacturer
            WHERE lower_name = %s
            LIMIT 1;
        '''
        result = self.query(query_str, (manufacturer_name,))

        # Sanity check, but there should be a result!
        if len(result) == 0:
            print(f'Error: No result for manfacturer name '
                  f'\'{manufacturer_name}\' found in production_manufacturer')
            self.close()
            sys.exit()

        return result[0]['id']

    def get_part_type_id(self: StdDatabase, part_type_name: str) -> int:
        """
        Get row ID of part type by lower case name.

        Returns:
            Primary key ID of part type.
        """
        part_type_name = part_type_name.lower()
        query_str = '''
            SELECT id
            FROM production_type
            WHERE lower_name = %s;
        '''
        result = self.query(query_str, (part_type_name,))

        # Sanity check, but there should be a result!
        if len(result) == 0:
            print(f'Error: No result for part type name '
                  f'\'{part_type_name}\' found in production_part')
            self.close()
            sys.exit()

        return result[0]['id']

    def insert_part(self: StdDatabase, system_id: str, part_model: str,
                    part_serial: str, type_id: str,
                    manufacturer_id: str) -> int:
        """
        Insert system part into STD, production_part.

        Args:
            system_id: Foreign key, production_system row id.
            part_model: Part number or model name.
            part_serial: Part serial number.
            type_id: Foreign key, part type row ID.
            manufacturer_id: Foreign key, manufacturer row ID.

        Returns:
            Inserted production_part row ID or existing ID.
        """
        # Check for existing entry first
        query_str = '''
            SELECT id
            FROM production_part
            WHERE system_id = %s
                AND model = %s
                AND serial = %s;
        '''
        result = self.query(query_str, (system_id, part_model, part_serial))

        # Insert new part if no ID returned from select
        if len(result) == 0:
            query_str = '''
                INSERT INTO production_part (system_id, serial,
                    model, type_id, manufacturer_id, intel_sa_number,
                    support_number, rma, revision, part_revision)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
            '''
            result = self.query(
                query_str,
                (
                    system_id,
                    part_serial,
                    part_model,
                    type_id, manufacturer_id,
                    '',
                    '',
                    False,
                    '',
                    '',
                )
            )

            # Save inserted ID to log
            key = 'production_part'
            if key in self.insert_log:
                self.insert_log[key].append(result[0]['id'])
            else:
                self.insert_log[key] = [result[0]['id']]

        return result[0]['id']

    def insert_mac(self: StdDatabase, system_id: str,
                   mac_name: str, mac_address: str) -> int:
        """
        Insert IPMI/NIC MAC addresses for system into STD, separated by colons.

        Args:
            system_id: Foreign key, production_system row id.
            mac_name: IPMI or NIC.
            mac_address: MAC address.

        Returns:
            Inserted production_mac row ID or existing ID.
        """
        # Catch poorly formatted MAC Address
        if len(mac_address) > 17:
            print(f'MAC Address exceeds 17 char: {mac_address}')
            self.close()
            sys.exit(1)

        # Check for existing entry first
        query_str = '''
            SELECT id
            FROM production_mac
            WHERE system_id = %s
                AND name = %s;
        '''
        result = self.query(query_str, (system_id, mac_name))

        # Insert new MAC if no ID returned from select
        if len(result) == 0:
            query_str = '''
                INSERT INTO production_mac (system_id, name, mac)
                VALUES (%s, %s, %s)
                RETURNING id;
            '''
            result = self.query(query_str, (system_id, mac_name, mac_address))

            # Save inserted ID to log
            key = 'production_mac'
            if key in self.insert_log:
                self.insert_log[key].append(result[0]['id'])
            else:
                self.insert_log[key] = [result[0]['id']]

        return result[0]['id']


def get_http_response_body(url: str) -> dict:
    """
    Send GET request and return JSON body parsed as python dict.

    Always return python dict, even if API call fails. This allows for lazy
    processing of the return value with dict.get(). Print error message but
    do not terminate script in case of HTTP errors. Auth header is basic
    authentication with credentials: USERID:Password123

    Args:
        url: URL target for GET request.

    Returns:
        JSON response body parsed to dict, or empty dict on HTTP error.
    """
    headers = {
        'Authorization': 'Basic VVNFUklEOlBhc3N3b3JkMTIz'
    }
    response = requests.get(url, headers=headers, verify=False)
    if response.ok:
        try:
            body = response.json()
            return body
        except requests.RequestsJSONDecodeError:
            print('Error: GET request had bad response for {url}')

    return {}


def parse_system_components(ip: str) -> dict:
    """
    Use Redfish to retrieve and parse component info.

    Args:
        ip: IPMI IP of system.

    Returns:
        Dict of part numbers and serials broken into part type.
    """
    # Dictionary of component info
    components = {
        'motherboard': {},
        'cpu': [],
        'memory': [],
        'psu': [],
        'ssd': [],
        'nic': [],
        'mac': [],
    }

    # Base URL is the IPMI IP of each system
    base_url = f'https://{ip}'

    # Motherboard
    chassis_url = f'{base_url}/redfish/v1/Chassis/1'
    body = get_http_response_body(chassis_url)
    motherboard_model = body.get('SKU', '')
    motherboard_serial = body.get('Oem', {}).get('Lenovo', {}).get('SystemBoardSerialNumber', '')
    components['motherboard'] = {
        'manufacturer': 'Lenovo',
        'model': motherboard_model,
        'serial': motherboard_serial,
    }

    # SSD
    # Reusing same HTTP response from motherboard code snippet above
    drive_list = body.get('Links', {}).get('Drives', [])

    if len(drive_list) == 0:
        print('Error: Drives Not Found')
    else:
        for drive in drive_list:
            drive_link = drive.get('@odata.id', '')
            body = get_http_response_body(base_url + drive_link)
            ssd_manufacturer = body.get('Manufacturer', '')
            ssd_model = body.get('Model', '')
            ssd_serial = body.get('SerialNumber', '')
            components['ssd'].append({
                'manufacturer': ssd_manufacturer,
                'model': ssd_model,
                'serial': ssd_serial,
            })

    # CPU
    cpu_url = f'{base_url}/redfish/v1/Systems/1/Processors'
    body = get_http_response_body(cpu_url)
    cpu_list = body.get('Members', [])

    if len(cpu_list) == 0:
        print('Error: CPU Not Found')
    else:
        for cpu in cpu_list:
            processor_link = cpu.get('@odata.id', '')
            body = get_http_response_body(base_url + processor_link)
            cpu_manufacturer = body.get('Manufacturer', '')
            cpu_model = body.get('Model', '')
            cpu_serial = body.get('ProcessorId', {}).get('ProtectedIdentificationNumber', '')
            components['cpu'].append({
                'manufacturer': cpu_manufacturer,
                'model': cpu_model,
                'serial': cpu_serial,
            })

    # Memory
    memory_url = f'{base_url}/redfish/v1/Systems/1/Memory'
    body = get_http_response_body(memory_url)
    memory_list = body.get('Members', [])

    if len(memory_list) == 0:
        print('Error: Memory Not Found')
    else:
        for memory in memory_list:
            memory_link = memory.get('@odata.id', '')
            body = get_http_response_body(base_url + memory_link)
            memory_manufacturer = body.get('Manufacturer', '')
            memory_model = body.get('PartNumber', '')
            memory_serial = body.get('SerialNumber', '')

            # Skip unpopulated slots
            if memory_model is None and memory_serial is None:
                continue

            components['memory'].append({
                'manufacturer': memory_manufacturer,
                'model': memory_model,
                'serial': memory_serial,
            })

    # PSU
    psu_url = f"{base_url}/redfish/v1/Chassis/1/Power"
    body = get_http_response_body(psu_url)
    psu_list = body.get('PowerSupplies', [])

    if len(psu_list) == 0:
        print('Error: PSU Not Found')
    else:
        for psu_body in psu_list:
            psu_manufacturer = psu_body.get('Manufacturer', '')
            psu_model = psu_body.get('PartNumber', '')
            psu_serial = psu_body.get('SerialNumber', '')
            components['psu'].append({
                'manufacturer': psu_manufacturer,
                'model': psu_model,
                'serial': psu_serial,
            })

    # NIC
    nic_url = f'{base_url}/redfish/v1/Chassis/1/NetworkAdapters'
    body = get_http_response_body(nic_url)
    nic_list = body.get('Members', [])

    if len(nic_list) == 0:
        print('Error: NIC Not Found')
    else:
        for nic in nic_list:
            nic_link = nic.get('@odata.id', '')
            body = get_http_response_body(base_url + nic_link)
            nic_manufacturer = body.get('Manufacturer', '')
            nic_model = body.get('Model', '')
            nic_serial = body.get('SerialNumber', '')
            components['nic'].append({
                'manufacturer': nic_manufacturer,
                'model': nic_model,
                'serial': nic_serial,
            })

    # NIC MAC
    nic_mac_url = f'{base_url}/redfish/v1/Systems/1/EthernetInterfaces'
    body = get_http_response_body(nic_mac_url)
    mac_list = body.get('Members', [])
    nic_number = 1

    if len(mac_list) == 0:
        print('Error: MAC Not Found')
    else:
        for mac in mac_list:
            mac_link = mac.get('@odata.id', '')

            # Skip Manager interface
            if 'NIC' not in mac_link:
                continue

            body = get_http_response_body(base_url + mac_link)
            mac_address = body.get('PermanentMACAddress', '')
            components['mac'].append({
                'name': f'NIC {nic_number} MAC',
                'mac': mac_address,
            })
            nic_number += 1

    # BMC MAC
    bmc_mac_url = f'{base_url}/redfish/v1/Managers/1/EthernetInterfaces/NIC'
    body = get_http_response_body(bmc_mac_url)
    mac_address = body.get('PermanentMACAddress', '')
    components['mac'].append({
        'name': 'IPMI 1 MAC',
        'mac': mac_address,
    })

    return components


def update_std(systems: dict):
    """
    Update STD for each system with all component info.

    System objects already expected to exist in STD and this script adds parts
    to those system objects. All or nothing.

    Args:
        systems: Dict of systems containing part info separated into part type.
    """
    with StdDatabase() as std:
        for ix_serial, components in systems.items():
            # Total rows inserted before this system
            pre_insertion_total = std.rows_inserted

            # Get system record ID from database
            system_id = std.get_system_id(ix_serial)

            # TODO: For each part type, insert a new part tying to system ID
            # Motherboard
            motherboard = components['motherboard']
            part_type = TYPE_DICT['motherboard']
            part_type_id = std.get_part_type_id(part_type)
            manufacturer = MANUFACTURER_DICT[motherboard['manufacturer'].lower()]
            manufacturer_id = std.get_manufacturer_id(manufacturer)
            std.insert_part(
                system_id,
                motherboard['model'],
                motherboard['serial'],
                part_type_id,
                manufacturer_id,
            )

            # CPU
            for cpu in components['cpu']:
                part_type = TYPE_DICT['cpu']
                part_type_id = std.get_part_type_id(part_type)
                manufacturer = MANUFACTURER_DICT[cpu['manufacturer'].lower()]
                manufacturer_id = std.get_manufacturer_id(manufacturer)
                std.insert_part(
                    system_id,
                    cpu['model'],
                    cpu['serial'],
                    part_type_id,
                    manufacturer_id,
                )

            # Memory
            for memory in components['memory']:
                part_type = TYPE_DICT['memory']
                part_type_id = std.get_part_type_id(part_type)
                manufacturer = MANUFACTURER_DICT[memory['manufacturer'].lower()]
                manufacturer_id = std.get_manufacturer_id(manufacturer)
                std.insert_part(
                    system_id,
                    memory['model'],
                    memory['serial'],
                    part_type_id,
                    manufacturer_id,
                )

            # PSU
            part_type = TYPE_DICT['psu']
            part_type_id = std.get_part_type_id(part_type)
            for psu in components['psu']:
                manufacturer = MANUFACTURER_DICT[psu['manufacturer'].lower()]
                manufacturer_id = std.get_manufacturer_id(manufacturer)
                std.insert_part(
                    system_id,
                    psu['model'],
                    psu['serial'],
                    part_type_id,
                    manufacturer_id,
                )

            # SSD
            part_type = TYPE_DICT['ssd']
            part_type_id = std.get_part_type_id(part_type)
            for ssd in components['ssd']:
                manufacturer = MANUFACTURER_DICT[ssd['manufacturer'].lower()]
                manufacturer_id = std.get_manufacturer_id(manufacturer)
                std.insert_part(
                    system_id,
                    ssd['model'],
                    ssd['serial'],
                    part_type_id,
                    manufacturer_id,
                )

            # NIC
            part_type = TYPE_DICT['nic']
            part_type_id = std.get_part_type_id(part_type)
            for nic in components['nic']:
                manufacturer = MANUFACTURER_DICT[nic['manufacturer'].lower()]
                manufacturer_id = std.get_manufacturer_id(manufacturer)
                std.insert_part(
                    system_id,
                    nic['model'],
                    nic['serial'],
                    part_type_id,
                    manufacturer_id,
                )

            # IPMI Password
            part_type = TYPE_DICT['password']
            part_type_id = std.get_part_type_id(part_type)
            manufacturer = MANUFACTURER_DICT['ixsystems']
            manufacturer_id = std.get_manufacturer_id(manufacturer)
            std.insert_part(
                system_id,
                'IPMI Password',
                'Password123',
                part_type_id,
                manufacturer_id,
            )

            # MAC addresses
            for mac in components['mac']:
                std.insert_mac(
                    system_id,
                    mac['name'],
                    mac['mac'].upper(),
                )

            # Print rows inserted for this system
            print(std.rows_inserted - pre_insertion_total, "rows inserted for system", ix_serial)

        # Print total rows inserted
        print(std.rows_inserted, "total rows inserted")
        std.commit()


if __name__ == '__main__':
    # Parse input file
    ip_list = []
    ix_serial_list = []
    with open(Path(__file__).with_name('KEY.txt')) as input_file:
        for line in input_file:
            # Skip commented lines
            if len(line) > 0 and line[0] == '#':
                continue

            # Split line and error if not exactly 2 tokens found
            tokens = line.strip().split()
            if len(tokens) != 2:
                print('Error: Input file has bad format')
                sys.exit(1)

            ip, ix_serial = tokens
            ip_list.append(ip)
            ix_serial_list.append(ix_serial)

    # Suppress InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

    # Store as dict of list of lists for part number and serials, example below
    """
    print(systems)
    {
        "A1-123456: {
            'cpu': [
                {
                    'manufacturer': 'Intel',
                    'model': 'partnumber123',
                    'serial': '8789678'
                },
                {
                    'manufacturer': 'Intel',
                    'model': 'partnumber123',
                    'serial': '2341345'
                },
            'memory': ...,
            'psu': ...,
            ...
        }
    }
    """
    systems = {}

    for ip, ix_serial in zip(ip_list, ix_serial_list):
        # To keep track of time elapsed per system
        start_time = time.time()

        # Parse system's Redfish for component info
        components = parse_system_components(ip)
        systems[ix_serial] = components
        print(f'{ix_serial} DONE - {(time.time() - start_time):.2f} seconds elapsed')

    # Save systems dict as JSON file
    with open(Path(__file__).with_name('lenovo_system_dump_.json'), 'w') as json_file:
        json.dump(systems, json_file)

    # Load JSON as python dictionary
    # with open(Path(__file__).with_name('lenovo_system_dump.json')) as json_file:
    #     systems = json.load(json_file)

    # Import gathered system component info to STD
    update_std(systems)
