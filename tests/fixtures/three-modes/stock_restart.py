from stock_check import request, reserve, run, server


# @case-id: TC-after-restart
def after_restart(binary, directory):
    with server(binary, directory) as port:
        status, original = reserve(port, 2)
        assert status == 201 and original["available"] == 8
    with server(binary, directory) as port:
        retry = reserve(port, 2)
        stock = request(port, "GET", "/stock")[1]["available"]
        print(f"retry={retry}; remaining={stock}", flush=True)
        assert retry == (200, original) and stock == 8, (retry, stock)


if __name__ == "__main__":
    run(after_restart)
