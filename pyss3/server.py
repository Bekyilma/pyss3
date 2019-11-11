# -*- coding: utf-8 -*-
"""
SS3 classification server with visual explanations for live tests.

(Please, visit https://github.com/sergioburdisso/pyss3 for more info)
"""
from __future__ import print_function
from os import path, listdir
from codecs import open
from tqdm import tqdm
from select import select
from datetime import datetime

from . import SS3
from .util import RecursiveDefaultDict, Print

import webbrowser
import argparse
import socket
import json
import re

# python 2 and 3 compatibility
try:
    from urllib.parse import unquote as url_decode
except ImportError:
    from urllib import unquote as url_decode


RECV_BUFFER = 1024 * 1024  # 1MB
HTTP_RESPONSE = ("HTTP/1.1 200 OK\r\n"
                 "Connection: close\r\n"
                 "Access-Control-Allow-Origin: *\r\n"
                 "Server: SS3\r\n"
                 "Content-type: %s\r\n"
                 "Content-length: %d\r\n\r\n")
HTTP_404 = ("HTTP/1.1 404 Not Found\r\n"
            "Connection: close\r\n"
            "Server: SS3\r\n"
            "Content-length: 0\r\n\r\n").encode()
CONTET_TYPE = {
    "html": "text/html",
    "css": "text/css",
    "js": "application/javascript",
    "png": "image/png",
    "json": "application/json",
    "ico": "image/vnd.microsoft.icon",
    "_other_": "application/octet-stream"
}

ENCODING = "utf-8"
BASE_PATH = path.join(path.dirname(__file__), "resources/visual_classifier")


def content_type(ext):
    """Given a file extension, return the content type."""
    return CONTET_TYPE[ext] if ext in CONTET_TYPE else CONTET_TYPE["_other_"]


def parse_and_sanitize(rsc_path):
    """Very simple function to parse and sanitize the given path."""
    dir, file = path.split(rsc_path)

    if not file:
        file = "index.html"

    ext = path.splitext(file)[1][1:]
    rsc_path = path.join(dir.replace('.', '').replace('//', '/'), file)[1:]
    return path.join(BASE_PATH, rsc_path), ext


def get_http_path(http_request):
    """Given a HTTP request, return the resource path."""
    return http_request.split('\n')[0].split(' ')[1]


def get_http_body(http_request):
    """Given a HTTP request, return the body."""
    return http_request.split("\r\n\r\n")[1]


def get_http_contlength(http_request):
    """Given a HTTP request, return the Content-Length value."""
    re_match = re.search(
        r"Content-length\s*:\s*(\d+)", http_request,
        flags=re.IGNORECASE
    )
    return int(re_match.group(1)) if re_match else 0


class Server:
    """SS3 HTTP server wrapper."""

    __port__ = 0  # any (free) port
    __clf__ = None
    __server_socket__ = None
    __docs__ = RecursiveDefaultDict()
    __x_test__ = None

    @staticmethod
    def __send_as_json__(sock, data):
        """Send the data as a json string."""
        data = json.dumps(data)
        http_header = HTTP_RESPONSE % (
            content_type("json"), len(data)
        )
        sock.send(http_header.encode())
        sock.send(data.encode(ENCODING))

    @staticmethod
    def __recvall_body__(sock, data, length):
        """Receive all HTTP message body."""
        body = get_http_body(data)
        while len(body) < length:
            body += sock.recv(RECV_BUFFER).decode()
        return url_decode(body)

    @staticmethod
    def __handle_request__(sock):
        """Handle browser request."""
        data = sock.recv(RECV_BUFFER)

        if not data:
            return

        data = data.decode()

        rsc_path = get_http_path(data)

        if data.startswith("POST"):
            Print.show("\tPOST %s" % rsc_path)
            method = rsc_path[1:]

            cont_length = get_http_contlength(data)
            body = Server.__recvall_body__(sock, data, cont_length)

            if method == "ack":
                Server.__do_ack__(sock)
            elif method == "classify":
                Server.__do_classify__(sock, body)
            elif method == "get_info":
                Server.__do_get_info__(sock)
            elif method == "get_doc":
                Server.__do_get_doc__(sock, body)

        else:  # if GET
            Print.show("\tGET %s " % rsc_path, False)

            local_path, ext = parse_and_sanitize(rsc_path)
            if path.exists(local_path):
                with open(local_path, 'rb') as fresponse:
                    http_body = fresponse.read()
                    http_header = HTTP_RESPONSE % (
                        content_type(ext), len(http_body)
                    )
                    sock.send(http_header.encode())
                    sock.send(http_body)
                    Print.info("200 OK")
            else:
                sock.send(HTTP_404)
                Print.info("404 Not Found")

    @staticmethod
    def __do_ack__(sock):
        """Serve the 'ack' message."""
        http_header = HTTP_RESPONSE % (content_type(''), 0)
        sock.send(http_header.encode())
        Print.info("sending ACK back to client...")

    @staticmethod
    def __do_classify__(sock, doc):
        """Serve the 'classify' message."""
        try:
            Print.show("\t%s[...]" % doc[:50])
            Server.__send_as_json__(
                sock,
                Server.__clf__.classify(doc, json=True)
            )
            Print.info("sending classification result...")
        except Exception as e:
            Print.error(str(e))

    @staticmethod
    def __do_get_info__(sock):
        """Serve the 'get_info' message."""
        clf = Server.__clf__
        Server.__send_as_json__(sock, {
            "model_name": clf.get_name(),
            "hps": clf.get_hyperparameters(),
            "categories": clf.get_categories() + ["[unknown]"],
            "docs": Server.__docs__
        })
        Print.info("sending classifier info...")

    @staticmethod
    def __do_get_doc__(sock, file):
        """Serve the 'get_doc' message."""
        doc = ""
        if ":line:" in file:
            file, line_n = file.split(":line:")
            line_n = int(line_n)
            with open(file, 'r', encoding=ENCODING) as fdoc:
                line = 0
                doc = fdoc.readline()
                while line < line_n:
                    doc = fdoc.readline()
                    line += 1
        elif ":x_test:" in file:
            if Server.__x_test__:
                idoc = int(file.split(":x_test:")[1])
                doc = Server.__x_test__[idoc]
        else:
            with open(file, 'r', encoding=ENCODING) as fdoc:
                doc = fdoc.read()

        Server.__send_as_json__(sock, {"content": doc})
        Print.info("sending document content...")

    @staticmethod
    def __clear_testset__():
        """Clear server's test documents."""
        Server.__docs__ = RecursiveDefaultDict()
        Server.__x_test__ = None

    @staticmethod
    def get_port():
        """
        Return the server port.

        :returns: the server port
        :rtype: int
        """
        return Server.__port__

    @staticmethod
    def set_model(clf):
        """
        Attach a given SS3 model to this server.

        :param clf: an SS3 model
        :type clf: pyss3.SS3
        """
        Server.__clf__ = clf
        Server.__clear_testset__()

    @staticmethod
    def set_testset(x_test, y_test):
        """
         Assign the test set to visualize.

        :param x_test: the list of documents to classify
        :type x_test: list (of str)
        :param y_label: the list of labels
        :type y_label: list (of str)
        """
        Server.__clear_testset__()
        Server.__x_test__ = x_test

        classify = Server.__clf__.classify
        unkwon_cat_i = len(Server.__clf__.get_categories())
        for cat in set(y_test):
            Server.__docs__[cat]["path"] = []
            Server.__docs__[cat]["file"] = []
            Server.__docs__[cat]["clf_result"] = []

        for idoc, doc in enumerate(x_test):
            cat = y_test[idoc]
            doc_name = "doc_%d" % idoc

            Server.__docs__[cat]["file"].append(doc_name)
            Server.__docs__[cat]["path"].append(":x_test:%d" % idoc)

            r = classify(doc)
            Server.__docs__[cat]["clf_result"].append(
                r[0][0] if r[0][1] else unkwon_cat_i
            )

        Print.info("%d categories found" % len(Server.__docs__))
        return len(Server.__docs__) > 0

    @staticmethod
    def set_testset_from_files(test_path, folder_label=True):
        """
        Load the test set files to visualize from ``test_path``.

        :param test_path: the test set path
        :type test_path: str
        :param folder_label: if True, read category labels from folders,
                             otherwise, read category labels from file names.
                             (default: True)
        :type folder_label: bool
        """
        Print.info("reading files...")
        Server.__clear_testset__()
        classify = Server.__clf__.classify
        unkwon_cat_i = len(Server.__clf__.get_categories())
        if not folder_label:
            for file in listdir(test_path):
                file_path = path.join(test_path, file)
                if path.isfile(file_path):
                    cat = path.splitext(file)[0]

                    with open(file_path, "r", encoding=ENCODING) as fcat:
                        Server.__docs__[cat]["clf_result"] = [
                            r[0][0] if r[0][1] else unkwon_cat_i
                            for r in map(
                                classify,
                                tqdm(
                                    fcat.readlines(),
                                    desc=" Classifying '%s' docs" % cat
                                )
                            )
                        ]
                        n_docs = len(Server.__docs__[cat]["clf_result"])
                        Server.__docs__[cat]["file"] = [
                            "doc_%d" % line
                            for line in range(n_docs)
                        ]
                        Server.__docs__[cat]["path"] = [
                            "%s:line:%d" % (file_path, line)
                            for line in range(n_docs)
                        ]
        else:
            for cat in listdir(test_path):
                cat_path = path.join(test_path, cat)

                if not path.isfile(cat_path):

                    Server.__docs__[cat]["path"] = []
                    Server.__docs__[cat]["file"] = []
                    Server.__docs__[cat]["clf_result"] = []

                    for file in tqdm(
                        sorted(listdir(cat_path)),
                        desc=" Classifying '%s' docs" % cat
                    ):
                        file_path = path.join(cat_path, file)
                        if path.isfile(file_path):
                            Server.__docs__[cat]["path"].append(file_path)
                            Server.__docs__[cat]["file"].append(file)
                            with open(
                                file_path, "r", encoding=ENCODING
                            ) as fdoc:
                                r = classify(fdoc.read())
                                Server.__docs__[cat]["clf_result"].append(
                                    r[0][0] if r[0][1] else unkwon_cat_i
                                )

        Print.info("%d categories found" % len(Server.__docs__))
        return len(Server.__docs__) > 0

    @staticmethod
    def start_listening(port=0):
        """
        Start listening on a port and return its number.

        (If a port number is not given, it uses a random free port).

        :param port: the port to listen on
        :type port: int
        """
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.bind(("0.0.0.0", port))
        server_socket.listen(128)

        Server.__server_socket__ = server_socket
        Server.__port__ = server_socket.getsockname()[1]

        Print.info(
            "SS3 server started (listening on port %d)"
            %
            Server.__port__
        )

        Print.warn(
            "Copy/paste this URL into your browser http://localhost:%d"
            %
            Server.__port__
        )
        Print.warn("Press Ctrl+C to stop the server\n")

        return Server.__port__

    @staticmethod
    def serve(
        clf=None, x_test=None, y_test=None, port=0, browser=True
    ):
        """
        Wait for classification requests and serve them.

        :param clf: the SS3 model to be attached to this server.
        :type clf: pyss3.SS3
        :param test_path: the test set path to visualize.
        :type test_path: str
        :param folder_label: if True, read category labels from folders,
                             otherwise, from file names. (default: True)
        :type folder_label: bool
        :param port: the port to listen on (default: random free port)
        :type port: int
        :param browser: if True, it automatically opens up the live test on
                        your browser
        :type browser: bool
        """
        Server.__clf__ = clf or Server.__clf__

        if not Server.__clf__:
            Print.error("a model must be given before serving")
            return

        if Server.__server_socket__ is None:
            Server.start_listening(port)

        if x_test:
            if y_test and len(y_test) == len(x_test):
                Server.set_testset(x_test, y_test)
            else:
                Print.error("y_test must have the same length as x_test")
                return

        server_socket = Server.__server_socket__
        clients = [server_socket]
        sock_to_addr = {}

        if browser:
            webbrowser.open("http://localhost:%d" % Server.__port__)

        Print.info("waiting for requests")
        print()

        try:
            while True:
                read_socks, write_socks, error_socks = select(clients, [], [])

                for sock in read_socks:

                    if sock is server_socket:
                        sockfd, addr = server_socket.accept()
                        clients.append(sockfd)
                        sock_to_addr[sockfd.fileno()] = addr[0]
                        Print.show(
                            Print.style.green("[ %s : %s ]")
                            % (addr[0], datetime.now())
                        )
                    else:
                        Server.__handle_request__(sock)
                        clients.remove(sock)
                        del sock_to_addr[sock.fileno()]
                        sock.close()
        except KeyboardInterrupt:
            Print.info("closing server...")
            server_socket.close()
            Server.__server_socket__ = None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='SS3 Server')

    parser.add_argument('MODEL', help="the model name")
    parser.add_argument('-ph', '--path', help="the test set path")
    parser.add_argument(
        '-l', '--label', choices=["file", "folder"], default="folder",
        help="indicates where to read category labels from"
    )
    parser.add_argument(
        '-p', '--port', type=int, default=0, help="the server port"
    )
    args = parser.parse_args()

    try:
        Print.warn(
            'SS3 Server comes with ABSOLUTELY NO WARRANTY. This is free software,'
            '\nand you are welcome to redistribute it under certain conditions'
            '\n(read license.txt for more details)\n', decorator=False
        )

        clf = SS3(name=args.MODEL)
        clf.load_model()

        Server.set_model(clf)
        Server.set_testset_from_files(args.path, args.label == 'folder')
        Server.serve(port=args.port, browser=False)
    except IOError:
        Print.error("No such model: '%s'" % args.MODEL)