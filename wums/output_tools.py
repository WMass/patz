import datetime
import glob
import os
import pathlib
import pickle
import re
import shutil
import subprocess
import sys
import tempfile
import json

import lz4.frame
import numpy as np

from wums import logging

logger = logging.child_logger(__name__)


def is_eosuser_path(path):
    if not path:
        return False
    path = os.path.realpath(path)
    return path.startswith("/eos/user") or path.startswith("/eos/home-")


def make_plot_dir(outpath, outfolder=None, eoscp=False, allowCreateLocalFolder=True):
    if eoscp and is_eosuser_path(outpath):
        # Create a unique temporary directory
        unique_temp_dir = tempfile.mkdtemp()
        outpath = os.path.join(unique_temp_dir, split_eos_path(outpath)[1])
        if not os.path.isdir(outpath):
            logger.info(f"Making temporary directory {outpath}")
            os.makedirs(outpath)

    full_outpath = outpath
    if outfolder:
        full_outpath = os.path.join(outpath, outfolder)
    if not full_outpath.endswith("/"):
        full_outpath += "/"
    if outpath and not os.path.isdir(outpath):
        # instead of raising, create folder to deal with cases where nested folders are created during code execution
        # (this would happen when outpath is already a path to a local subfolder not created in the very beginning)
        if allowCreateLocalFolder:
            logger.debug(f"Creating new directory {outpath}")
            os.makedirs(outpath)
        else:
            raise IOError(
                f"The path {outpath} doesn't not exist. You should create it (and possibly link it to your web area)"
            )

    if full_outpath and not os.path.isdir(full_outpath):
        try:
            os.makedirs(full_outpath)
            logger.info(f"Creating folder {full_outpath}")
        except FileExistsError as e:
            logger.warning(e)

    return full_outpath


def copy_to_eos(tmpFolder, outpath, outfolder=None, deleteFullTmp=False):
    eospath, outpath = split_eos_path(outpath)
    fullpath = outpath
    if outfolder:
        fullpath = os.path.join(outpath, outfolder)
    logger.info(f"Copying {tmpFolder} to {eospath}")

    for f in glob.glob(tmpFolder + "/*"):
        if not (os.path.isfile(f) or os.path.isdir(f)):
            continue
        outPathForCopy = "/".join(
            ["root://eosuser.cern.ch", eospath, f.replace(tmpFolder, f"{fullpath}/")]
        )
        if os.path.isdir(f):
            # remove last folder to do "xrdcp -fr /path/to/folder/ root://eosuser.cern.ch//eos/cms/path/to/"
            # in this way one can copy the whole subfolder through xrdcp without first creating the structure
            outPathForCopy = os.path.dirname(outPathForCopy.rstrip("/"))
        command = ["xrdcp", "-fr", f, outPathForCopy]

        logger.debug(f"Executing {' '.join(command)}")
        if subprocess.call(command):
            raise IOError(
                "Failed to copy the files to eos! Perhaps you are missing a kerberos ticket and need to run kinit <user>@CERN.CH?"
                " from lxplus you can run without eoscp and take your luck with the mount."
            )

    shutil.rmtree(tmpFolder.replace(fullpath, ""))


def split_eos_path(path):

    path = os.path.realpath(path)
    if not is_eosuser_path(path):
        raise ValueError(f"Expected a path on /eos/user, found {path}!")

    splitpath = [x for x in path.split("/") if x]
    # Can be /eos/user/<letter>/<username> or <letter-username>
    if "home-" in splitpath[1]:
        eospath = "/".join(["/eos/user", splitpath[1].split("-")[-1], splitpath[2]])
        basepath = "/".join(splitpath[3:])
    else:
        eospath = "/".join(splitpath[:4])
        basepath = "/".join(splitpath[4:])

    if path[0] == "/":
        eospath = "/" + eospath

    return eospath, basepath


def script_command_to_str(argv, parser_args):
    call_args = np.array(argv[1:], dtype=object)
    match_expr = "|".join(
        ["^-+([a-z]+[1-9]*-*)+"]
        + (
            []
            if not parser_args
            else [f"^-*{x.replace('_', '.')}" for x in vars(parser_args).keys()]
        )
    )
    if call_args.size != 0:
        flags = np.vectorize(lambda x: bool(re.match(match_expr, x)))(call_args)
        special_chars = np.vectorize(lambda x: not x.isalnum())(call_args)
        select = ~flags & special_chars
        if np.count_nonzero(select):
            call_args[select] = np.vectorize(lambda x: f"'{x}'")(call_args[select])
    return " ".join([argv[0], *call_args])


def make_meta_info_dict(
    exclude_diff="notebooks", args=None, wd=f"{pathlib.Path(__file__).parent}/../"
):
    meta_data = {
        "time": str(datetime.datetime.now()),
        "command": script_command_to_str(sys.argv, args),
        "args": {a: getattr(args, a) for a in vars(args)} if args else {},
    }
    if (
        subprocess.call(
            ["git", "branch"],
            cwd=wd,
            stderr=subprocess.STDOUT,
            stdout=open(os.devnull, "w"),
        )
        != 0
    ):
        meta_data["git_info"] = {
            "hash": "Not a git repository!",
            "diff": "Not a git repository",
        }
    else:
        meta_data["git_hash"] = subprocess.check_output(
            ["git", "log", "-1", '--format="%H"'], cwd=wd, encoding="UTF-8"
        )
        diff_comm = ["git", "diff"]
        if exclude_diff:
            diff_comm.extend(["--", f":!{exclude_diff}"])
        meta_data["git_diff"] = subprocess.check_output(
            diff_comm, encoding="UTF-8", cwd=wd
        )

    return meta_data


def write_logfile(
    outpath,
    logname,
    args={},
    meta_info={},
    wd = f"{pathlib.Path(__file__).parent}/../",
):
    logname = f"{outpath}/{logname}.log"

    with open(logname, "w") as logf:
        info = make_meta_info_dict(args=args, wd=wd)

        for k, v in {**info, **meta_info}.items():
            logf.write("\n" + "-" * 80 + "\n")
            if isinstance(v, dict):
                logf.write(k)
                logf.write(json.dumps(v, indent=5).replace("\\n", "\n"))
            else:
                logf.write(f"{k}: {v}\n")


def write_index_and_log(
    outpath,
    logname,
    template_dir=f"{pathlib.Path(__file__).parent}/Templates",
    yield_tables=None,
    analysis_meta_info=None,
    args={},
    nround=2,
    wd=f"{pathlib.Path(__file__).parent}/../",
):
    indexname = "index.php"
    if "mit.edu" in socket.gethostname() and not (
        hasattr(args, "eoscp") and args.eoscp
    ):
        indexname = "index_mit.php"

    shutil.copyfile(f"{template_dir}/{indexname}", f"{outpath}/index.php")
    logname = f"{outpath}/{logname}.log"

    with open(logname, "w") as logf:
        meta_info = (
            "-" * 80
            + "\n"
            + f"Script called at {datetime.datetime.now()}\n"
            + f"The command was: {script_command_to_str(sys.argv, args)}\n"
            + "-" * 80
            + "\n"
        )
        logf.write(meta_info)
        meta_info = make_meta_info_dict(
            "notebooks",
            args=args,
            wd=wd,
        )
        logf.write(f"git hash: {meta_info['git_hash']}\n")
        logf.write(f"git diff: {meta_info['git_diff']}\n")

        if yield_tables:
            for k, v in yield_tables.items():
                logf.write(f"Yield information for {k}\n")
                logf.write("-" * 80 + "\n")
                logf.write(str(v.round(nround)) + "\n\n")

            if (
                "Unstacked processes" in yield_tables
                and "Stacked processes" in yield_tables
            ):
                if "Data" in yield_tables["Unstacked processes"]["Process"].values:
                    unstacked = yield_tables["Unstacked processes"]
                    data_yield = unstacked[unstacked["Process"] == "Data"][
                        "Yield"
                    ].iloc[0]
                    ratio = (
                        float(
                            yield_tables["Stacked processes"]["Yield"].sum()
                            / data_yield
                        )
                        * 100
                    )
                    logf.write(f"===> Sum unstacked to data is {ratio:.2f}%")

        if analysis_meta_info:
            for k, analysis_info in analysis_meta_info.items():
                logf.write("\n" + "-" * 80 + "\n")
                logf.write(f"Meta info from input file {k}\n")
                logf.write("\n" + "-" * 80 + "\n")
                logf.write(json.dumps(analysis_info, indent=5).replace("\\n", "\n"))
        logger.info(f"Writing file {logname}")