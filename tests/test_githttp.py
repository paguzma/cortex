from cortex.web.githttp import parse_cgi


def test_parse_cgi():
    status, headers, body = parse_cgi(b"Status: 403 Forbidden\r\nContent-Type: text/plain\r\n\r\nno")
    assert status == 403 and headers == {"Content-Type": "text/plain"} and body == b"no"
    assert parse_cgi(b"Content-Type: x\n\nbody")[0] == 200
    assert parse_cgi(b"sin separador")[0] == 502
