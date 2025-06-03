import requests
from requests.auth import HTTPBasicAuth
import getpass

jenkins_url = "https://jenkins-test.atros.com.tr"
username = "cagatay.soyler"
password = getpass.getpass("Jenkins şifrenizi girin ({}): ".format(username))
job_name = "ornek-job"

job_config_xml = """<?xml version='1.1' encoding='UTF-8'?>
<project>
  <description>Python'dan oluşturulan iş</description>
  <builders>
    <hudson.tasks.Shell>
      <command>echo Merhaba Jenkins</command>
    </hudson.tasks.Shell>
  </builders>
</project>
"""

# Oturum başlat
session = requests.Session()
session.auth = HTTPBasicAuth(username, password)

# Crumb al
crumb_url = f"{jenkins_url}/crumbIssuer/api/json"
crumb_response = session.get(crumb_url)
if crumb_response.status_code != 200:
    print("Crumb alınamadı:", crumb_response.status_code, crumb_response.text)
    exit()

crumb_data = crumb_response.json()
crumb_field = crumb_data["crumbRequestField"]
crumb_value = crumb_data["crumb"]
headers = {
    "Content-Type": "application/xml",
    crumb_field: crumb_value
}

# Job oluştur
create_url = f"{jenkins_url}/createItem?name={job_name}"
response = session.post(create_url, data=job_config_xml, headers=headers)

if response.status_code == 200:
    print("Job başarıyla oluşturuldu.")
elif response.status_code == 400:
    print("Job zaten var.")
else:
    print(f"Hata oluştu: {response.status_code}\n{response.text}")