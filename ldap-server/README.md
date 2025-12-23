# Local Samba LDAP Server (NTLM)

This directory contains the files to deploy a containerized Samba AD DC on OpenShift for LDAP NTLM testing.

## Deployment Instructions

1. **Create the infrastructure**:
   ```bash
   oc create -f openshift/pvc.yaml
   oc create -f openshift/service.yaml
   ```

2. **Build the image**:
   ```bash
   oc new-build --binary --name=ldap-server -l app=ldap-server
   oc start-build ldap-server --from-dir=. --follow
   ```

3. **Deploy**:
   ```bash
   oc apply -f openshift/deployment.yaml
   ```

4. **Permissions (Important)**:
   Samba requires root to manage file system ACLs. You may need to grant `anyuid` SCC to the default service account in your namespace:
   ```bash
   oc adm policy add-scc-to-user anyuid -z default
   ```

## Test Credentials
- **Domain**: `LAB.LOCAL`
- **User**: `testuser`
- **Password**: `Password123!`
- **LDAP URL**: `ldap://ldap-server:389`
- **Search Base**: `CN=Users,DC=LAB,DC=LOCAL`
