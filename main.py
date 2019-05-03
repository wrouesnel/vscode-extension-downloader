#!/usr/bin/env python3
# Script to mirror the VSCode extensions repository

# Sample download URL of a package:
# https://marketplace.visualstudio.com/_apis/public/gallery/publishers/rebornix/vsextensions/Ruby/0.22.3/vspackage
# transliterated: https://marketplace.visualstudio.com/_apis/public/gallery/publishers/{publisherName}/vsextensions/{name}/{version}/vspackage

import os
import sys
import subprocess
import json
import requests
from collections import defaultdict
from retrying import retry
import click
import structlog

from pprint import pprint

logger = structlog.get_logger()

s = requests.Session()


def extqry(pageNumber):
    """Constructs a query to get a given page of extensions"""
    return {
        "assetTypes": [],
        "filters": [
            {
                "criteria": [
                    {"filterType": 8, "value": "Microsoft.VisualStudio.Code"},
                    {
                        "filterType": 10,
                        "value": 'target:"Microsoft.VisualStudio.Code" ',
                    },
                    {"filterType": 12, "value": "37888"},
                ],
                "direction": 2,
                "pageSize": 54,
                "pageNumber": pageNumber,
                "sortBy": 10,
                "sortOrder": 0,
                "pagingToken": None,
            }
        ],
        "flags": 870,
    }


params = {"api-version": "5.1-preview.1"}


class ExtensionEndpointError(Exception):
    """Wrapper for errors"""

    def __init__(self, data, *args, **kwargs):
        self.data = data
        super().__init__(*args, **kwargs)


def retry_manager_fn(exc):
    if isinstance(exc, (ExtensionEndpointError,)):
        if "typeKey" in exc.data:
            logger.info("Got Microsoft Endpoint Error")
            if exc.data["typeKey"] == "CircuitBreakerExceededExecutionLimitException":
                logger.info("Got circuit breaker exception, allowing back off.")
                return True
            else:
                pprint(exc.data)
                logger.bind(exc_data=exc.data).error("Unknown structured error")
    return False


@retry(
    retry_on_exception=retry_manager_fn,
    wait_exponential_multiplier=1000,
    wait_exponential_max=60000,
)
def post_extension_query(page_number):
    r = s.post(
        "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery",
        params=params,
        json=extqry(page_number),
    )
    if not r.ok:
        raise ExtensionEndpointError(r.json(), "Got not okay response with JSON body")
    return r.json()


def get_vspackage_path(publisher_name, extension_name, version):
    return "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/{publisherName}/vsextensions/{name}/{version}/vspackage".format(
        publisherName=publisher_name, name=extension_name, version=version
    )


@click.group()
def cli():
    """Utility to build mirrors of VSCode extensions"""
    pass


@cli.command("print-download-links")
@click.option("--index-file", default="index.json", type=click.File("rt"))
def print_download_links(index_file):
    """Use an index of extensions to print VSIX links"""

    logger.info("Loading index file")
    index = json.load(index_file)

    for publisher_name, extension_dict in index.items():
        for extension_name, versions in extension_dict.items():
            for version in versions:
                download_path = get_vspackage_path(
                    publisher_name, extension_name, version
                )
                sys.stdout.write(download_path + "\n")


@cli.command("mirror-extensions")
@click.option("--index-file", default="index.json", type=click.File("rt"))
@click.option("--output-dir", default="vscode-extensions", type=click.STRING)
def mirror_extensions(index_file, output_dir):
    """Use an index of extensions to download VSIX files for extensions"""

    logger.info("Loading index file")
    index = json.load(index_file)
    logger.info("Loaded index file")

    os.makedirs(output_dir, exist_ok=True)

    for publisher_name, extension_dict in sorted(index.items()):
        for extension_name, versions in sorted(extension_dict.items()):
            for version in sorted(versions, reverse=True):
                download_path = get_vspackage_path(
                    publisher_name, extension_name, version
                )

                log = logger.bind(
                    publisher_name=publisher_name,
                    extension_name=extension_name,
                    version=version,
                )
                log.info("Downloading extension")

                try:
                    subprocess.check_call(
                        ["wget", "--content-disposition", download_path], cwd=output_dir
                    )
                except subprocess.CalledProcessError:
                    log.error("Failed downloading package")


@cli.command("download-index")
@click.option("--index-file", default="index.json", type=click.File("wt"))
def download_index(index_file):
    """Download index of data. Prerequisite for mirroring extensions."""
    # Dictionary of extension data
    extension_data = defaultdict(lambda: defaultdict(list))

    # Initial page number of results
    pageNumber = 1

    while True:
        log = logger.bind(page_number=pageNumber)
        log.info("Querying for extensions")
        result_data = post_extension_query(pageNumber)

        if "results" not in result_data:
            log.info("Finished iterating extensions")
            break

        extensions = result_data["results"][0]["extensions"]
        received_extensions = len(extensions)

        log.bind(received_extensions=received_extensions).info(
            "Got results - only iterating extensions"
        )

        if received_extensions == 0:
            log.info("No extensions received. Ending iteration.")
            break

        for i in extensions:
            for version in i["versions"]:
                extension_data[i["publisher"]["publisherName"]][
                    i["extensionName"]
                ].append(version["version"])

        pageNumber += 1

    logger.bind(extension_count=len(extension_data)).info("Got extensions")

    json.dump(extension_data, index_file, sort_keys=True)
    logger.info("Index download complete.")


if __name__ == "__main__":
    cli()
