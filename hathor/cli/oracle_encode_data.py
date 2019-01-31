import base64
import struct

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from hathor.crypto.util import get_private_key_from_bytes


def main():
    from hathor.cli.util import create_parser
    parser = create_parser()

    parser.add_argument('data', nargs='+', help='Encode data in oracle format.')
    parser.add_argument('--keyfile', help='Path to a private key file, used to sign the data')
    args = parser.parse_args()

    binary_data = b''

    for d in args.data:
        [t, _data] = d.split(':')
        if t == 'int':
            b = encode_int(_data)
        elif t == 'str':
            b = encode_str(_data)
        else:
            print('wrong data type {}'.format(d))
            return 1

        binary_data += (bytes([len(b)]) + b)

    print('data (base64):', base64.b64encode(binary_data).decode('utf-8'))

    with open(args.keyfile, 'r') as key_file:
        private_key_bytes = base64.b64decode(key_file.read())
    private_key = get_private_key_from_bytes(private_key_bytes)
    signature = private_key.sign(binary_data, ec.ECDSA(hashes.SHA256()))
    print('signature (base64):', base64.b64encode(signature).decode('utf-8'))


def encode_int(data):
    d = int(data)
    if d > 4294967295:
        n = struct.pack('!Q', d)
    elif d > 65535:
        n = struct.pack('!I', d)
    elif d > 255:
        n = struct.pack('!H', d)
    else:
        n = struct.pack('!B', d)
    return n


def encode_str(data):
    if isinstance(data, int):
        data = str(data)
    return data.encode('utf-8')
