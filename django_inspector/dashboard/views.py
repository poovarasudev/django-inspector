from django.shortcuts import render


def live_feed(request):
    return render(request, "inspector/live_feed.html")


def requests_list(request):
    return render(request, "inspector/requests/list.html")


def request_detail(request, pk):
    return render(request, "inspector/requests/detail.html", {"pk": pk})


def queries_list(request):
    return render(request, "inspector/queries/list.html")


def query_detail(request, pk):
    return render(request, "inspector/queries/detail.html", {"pk": pk})


def exceptions_list(request):
    return render(request, "inspector/exceptions/list.html")


def exception_detail(request, pk):
    return render(request, "inspector/exceptions/detail.html", {"pk": pk})
