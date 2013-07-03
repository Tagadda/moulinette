# -*- coding: utf-8 -*-

import os
import sys
import yaml
import re
import getpass
from yunohost import YunoHostError, YunoHostLDAP, validate, colorize, get_required_args, win_msg
from yunohost_domain import domain_add
from yunohost_dyndns import dyndns_subscribe

def tools_ldapinit():
    """
    Initialize YunoHost LDAP scheme

    Returns:
        dict

    """
    with YunoHostLDAP() as yldap:

        with open('ldap_scheme.yml') as f:
            ldap_map = yaml.load(f)

        for rdn, attr_dict in ldap_map['parents'].items():
            yldap.add(rdn, attr_dict)

        for rdn, attr_dict in ldap_map['children'].items():
            yldap.add(rdn, attr_dict)

        admin_dict = {
            'cn': 'admin',
            'uid': 'admin',
            'description': 'LDAP Administrator',
            'gidNumber': '1007',
            'uidNumber': '1007',
            'homeDirectory': '/home/admin',
            'loginShell': '/bin/bash',
            'objectClass': ['organizationalRole', 'posixAccount', 'simpleSecurityObject']
        }

        yldap.update('cn=admin', admin_dict)

    win_msg(_("LDAP has been successfully initialized"))


def tools_adminpw(old_password, new_password):
    """
    Change admin password

    Keyword arguments:
        old_password
        new_password

    Returns:
        dict

    """
    # Validate password length
    if len(new_password) < 4:
        raise YunoHostError(22, _("Password is too short"))

    result = os.system('ldappasswd -h localhost -D cn=admin,dc=yunohost,dc=org -w "'+ old_password +'" -a "'+ old_password +'" -s "' + new_password + '"')

    if result == 0:
        win_msg(_("Admin password has been changed"))
    else:
        raise YunoHostError(22, _("Invalid password"))


def tools_maindomain(old_domain, new_domain):
    """
    Change admin password

    Keyword arguments:
        old_domain
        new_domain

    Returns:
        dict

    """

    if not old_domain:
        with open('/etc/yunohost/current_host', 'r') as f:
            old_domain = f.readline().rstrip()

    validate(r'^([a-zA-Z0-9]{1}([a-zA-Z0-9\-]*[a-zA-Z0-9])*)(\.[a-zA-Z0-9]{1}([a-zA-Z0-9\-]*[a-zA-Z0-9])*)*(\.[a-zA-Z]{1}([a-zA-Z0-9\-]*[a-zA-Z0-9])*)$', old_domain)

    config_files = [
        '/etc/postfix/main.cf',
        '/etc/metronome/metronome.cfg.lua',
        '/etc/dovecot/dovecot.conf',
        '/etc/lemonldap-ng/lemonldap-ng.ini',
        '/etc/hosts',
        '/usr/share/yunohost/yunohost-config/others/startup',
    ]

    config_dir = []

    for dir in config_dir:
        for file in os.listdir(dir):
            config_files.append(dir + '/' + file)

    for file in config_files:
        with open(file, "r") as sources:
            lines = sources.readlines()
        with open(file, "w") as sources:
            for line in lines:
                sources.write(re.sub(r''+ old_domain +'', new_domain, line))

    domain_add([new_domain], raw=False, main=True)

    lemon_conf_lines = [
        "$tmp->{'domain'} = '"+ new_domain +"';", # Replace Lemon domain
        "$tmp->{'ldapBase'} = 'dc=yunohost,dc=org';", # Set ldap basedn
        "$tmp->{'portal'} = 'https://"+ new_domain +"/sso/';", # Set SSO url
        "$tmp->{'locationRules'}->{'"+ new_domain +"'}->{'(?#0ynh_admin)^/ynh-admin/'} = '$uid eq \"admin\"';",
        "$tmp->{'locationRules'}->{'"+ new_domain +"'}->{'(?#0ynh_user)^/ynh-user/'} = '$uid ne \"admin\"';"
    ]

    if old_domain is not 'yunohost.org':
        lemon_conf_lines.extend([
            "delete $tmp->{'locationRules'}->{'"+ old_domain +"'}->{'(?#0ynh_admin)^/ynh-admin/'};",
            "delete $tmp->{'locationRules'}->{'"+ old_domain +"'}->{'(?#0ynh_user)^/ynh-user/'};"
        ])

    with open('/tmp/tmplemonconf','w') as lemon_conf:
        for line in lemon_conf_lines:
            lemon_conf.write(line + '\n')

    os.system('rm /etc/yunohost/apache/domains/' + old_domain + '.d/*.fixed.conf') # remove SSO apache conf dir from old domain conf (fail if postinstall)
    os.system('rm /etc/ssl/private/yunohost_key.pem')
    os.system('rm /etc/ssl/certs/yunohost_crt.pem')

    command_list = [
        'cp /etc/yunohost/apache/templates/sso.fixed.conf   /etc/yunohost/apache/domains/' + new_domain + '.d/sso.fixed.conf', # add SSO apache conf dir to new domain conf
        'cp /etc/yunohost/apache/templates/admin.fixed.conf /etc/yunohost/apache/domains/' + new_domain + '.d/admin.fixed.conf',
        'cp /etc/yunohost/apache/templates/user.fixed.conf  /etc/yunohost/apache/domains/' + new_domain + '.d/user.fixed.conf',
        '/usr/share/lemonldap-ng/bin/lmYnhMoulinette',
        '/etc/init.d/hostname.sh',
        'cp    /etc/yunohost/certs/'+ new_domain +'/key.pem /etc/metronome/certs/yunohost_key.pem',
        'chown metronome: /etc/metronome/certs/yunohost_key.pem',
        'ln -s /etc/yunohost/certs/'+ new_domain +'/key.pem /etc/ssl/private/yunohost_key.pem',
        'ln -s /etc/yunohost/certs/'+ new_domain +'/crt.pem /etc/ssl/certs/yunohost_crt.pem',
        'echo '+ new_domain +' > /etc/yunohost/current_host',
        'service apache2 reload',
        'service metronome restart',
        'service postfix restart'
    ]

    for command in command_list:
        if os.system(command) != 0:
            raise YunoHostError(17, _("There were a problem during domain changing"))

    win_msg(_("Main domain has been successfully changed"))


def tools_postinstall(domain, password, dyndns=False):
    """
    Post-install configuration

    Keyword arguments:
        domain -- Main domain
        password -- New admin password

    Returns:
        dict

    """
    with YunoHostLDAP(password='yunohost') as yldap:
        try:
            with open('/etc/yunohost/installed') as f: pass
        except IOError:
            print('Installing YunoHost')
        else:
            raise YunoHostError(17, _("YunoHost is already installed"))

        # Create required folders
        folders_to_create = [
            '/etc/yunohost/apps',
            '/etc/yunohost/certs',
            '/var/cache/yunohost/repo'
        ]

        for folder in folders_to_create:
            try: os.listdir(folder)
            except OSError: os.makedirs(folder)

        # Create SSL CA
        ssl_dir = '/usr/share/yunohost/yunohost-config/ssl/yunoCA'
        command_list = [
            'echo "01" > '+ ssl_dir +'/serial',
            'rm '+ ssl_dir +'/index.txt',
            'touch '+ ssl_dir +'/index.txt',
            'openssl req -x509 -new -config '+ ssl_dir +'/openssl.cnf -days 3650 -out '+ ssl_dir +'/ca/cacert.pem -keyout '+ ssl_dir +'/ca/cakey.pem -nodes -batch',
            'cp '+ ssl_dir +'/ca/cacert.pem /etc/ssl/certs/ca-yunohost_crt.pem',
            'update-ca-certificates'
        ]

        for command in command_list:
            if os.system(command) != 0:
                raise YunoHostError(17, _("There were a problem during CA creation"))

        # Initialize YunoHost LDAP base
        tools_ldapinit()

        # New domain config
        tools_maindomain(old_domain='yunohost.org', new_domain=domain)

        # Change LDAP admin password
        tools_adminpw(old_password='yunohost', new_password=password)

        if dyndns: dyndns_subscribe()

        os.system('touch /etc/yunohost/installed')

    win_msg(_("YunoHost has been successfully configured"))
