#!/usr/bin/env python3
"""Judgement scorekeeper as a desktop app: the local server inside a native window."""
import webview

import server


def main():
    url = server.start_in_background()
    webview.create_window(
        "Judgement Scorekeeper", url,
        width=1320, height=900, min_size=(760, 600),
    )
    webview.start()


if __name__ == "__main__":
    main()
