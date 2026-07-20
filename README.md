./env/bin/pip install -e apps/wxwork_login

bench --site site1.local install-app wxwork_login

bench --site site1.local migrate

bench build

bench restart
