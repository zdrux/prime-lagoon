from ldap3 import Server, Connection, ALL
from typing import Optional
from sqlmodel import Session, select
from app.models import AppConfig

from ldap3 import Server, Connection, ALL, Tls
import ssl

def get_ldap_config(session: Session):
    config = session.exec(select(AppConfig)).all()
    cfg_dict = {c.key: c.value for c in config}
    return cfg_dict

def authenticate_ldap(username, password, session: Session) -> bool:
    cfg = get_ldap_config(session)
    ldap_host = cfg.get("LDAP_HOST")
    ldap_port = cfg.get("LDAP_PORT") or 389
    use_ssl = cfg.get("LDAP_USE_SSL") == "True"
    bind_dn = cfg.get("LDAP_BIND_DN")
    bind_pw = cfg.get("LDAP_BIND_PASSWORD")
    search_base = cfg.get("LDAP_USER_SEARCH_BASE")
    user_filter_tpl = cfg.get("LDAP_USER_FILTER", "(uid={username})")
    
    if not ldap_host:
        return False
        
    try:
        tls = None
        if use_ssl:
            tls = Tls(validate=ssl.CERT_NONE)
            
        server = Server(ldap_host, port=int(ldap_port), use_ssl=use_ssl, tls=tls, get_info=ALL)
        
        user_str = username
        ldap_domain = cfg.get("LDAP_USER_DOMAIN")
        if ldap_domain:
            user_str = f"{ldap_domain}\\{username}"
            
        auth_type = cfg.get("LDAP_AUTH_TYPE", "SIMPLE")
        
        from ldap3 import NTLM
        
        if auth_type == "NTLM":
            conn = Connection(server, user=user_str, password=password, authentication=NTLM)
        else:
            conn = Connection(server, user=user_str, password=password)
            
        return conn.bind()
            
    except Exception as e:
        print(f"LDAP Auth Error for {username}: {e}")
        return False
