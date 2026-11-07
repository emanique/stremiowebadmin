#!/usr/bin/env python3
from pathlib import Path
import ipaddress,json,os,subprocess,sys,tempfile
def discover():
 d=json.loads(subprocess.check_output(["ip","-j","-4","route","get","1.1.1.1"],text=True))
 if len(d)!=1 or not d[0].get("dev"):raise RuntimeError("no egress interface")
 entries=subprocess.check_output(["resolvectl","dns",d[0]["dev"]],text=True).partition(":")[2]
 for entry in entries.split():
  try:addr=ipaddress.IPv4Address(entry)
  except ValueError:continue
  if not (addr.is_loopback or addr.is_multicast or addr.is_unspecified):return str(addr)
 raise RuntimeError("no usable host DNS")
def persist(path,dns):
 if path.is_symlink() or not path.is_file():raise ValueError("untrusted env")
 original=path.read_text()
 lines=original.splitlines()
 n=sum(line.startswith("STREMIO_DNS_SERVER=") for line in lines)
 if n>1:raise ValueError("duplicate DNS settings")
 lines=[("STREMIO_DNS_SERVER="+dns) if line.startswith("STREMIO_DNS_SERVER=") else line for line in lines]
 if not n:lines.append("STREMIO_DNS_SERVER="+dns)
 updated="\n".join(lines)+"\n"
 if updated==original:return False
 st=path.stat();fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=".dns-")
 try:
  os.fchmod(fd,st.st_mode & 0o777);os.fchown(fd,st.st_uid,st.st_gid)
  with os.fdopen(fd,"w") as stream:
   stream.write(updated);stream.flush();os.fsync(stream.fileno())
  os.replace(tmp,path)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
 return True
if __name__=="__main__":
 dns=discover(); print("DNS",dns,"changed",persist(Path(sys.argv[1]),dns))
