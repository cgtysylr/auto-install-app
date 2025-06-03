import requests
from requests.auth import HTTPBasicAuth
import getpass
from flask import Flask, render_template, request, jsonify
import os

app = Flask(__name__)

# Jenkins bilgileri
JENKINS_URL = "https://jenkins-test.atros.com.tr"
USERNAME = "cagatay.soyler"
PASSWORD = getpass.getpass("Jenkins şifrenizi girin ({}): ".format(USERNAME))

# --- JENKINSFILE TEMPLATE ---
JENKINSFILE_TEMPLATE = '''
// Which branches to trigger the pipeline upon Bitbucket webhook
def webhookGitBranchFilter = "{filter_branch}"

// Which branches to allow the pipeline to run
def allowedBranches = [{allowed_branches}]

// allowAutoDeploymentForBranches: Which branches will be automatically deployed after the build step
def allowAutoDeploymentForBranches = []


def runSonarScanPipelineConfig = [
    non_merged_pull_request: true,  // run sonar scan for non-merged pull requests
    merged_pull_request: true,     // run sonar scan for merged pull requests
    manual_trigger: true,           // run sonar scan for manually triggered builds
]

def _doRunSonarQube = false  // internal variable, do not change

def pipelineVariables = [
    envParamInput: "Deployment Environment",
    imageName: "{image_name}",
    artifactTAG: "",
    envToDeploy: null,
    dockerCredentialsId: "dockerhub-secret",
    dockerfile: "{dockerfile_path}",
    dockerBuildArg: "",  // optional
]

repo = [
    selectedBranch: "master",                 // TODO: update this
    gitURL: "{git_url}",
    credentialsId: "jenkins-bitbucket-cloud-ro-id",
    parameterProps: [
        defaultValue: 'master',
        name: '{artifact_name} Branch Selection',
    ],
    section: [
        name: "{artifact_name}_Section",
        header: "{artifact_name} Branch Parameters",
        sectionHeaderStyle: " position: relative;  font-weight: 900;  font: bold 24px/1 Roboto, sans-serif !important;  padding-bottom: 10px;",
    ],
    branchPrefix: "refs/heads/",
    runInputPrefix: "Trigger Job Manualy for Selected Branch",
    hubCreds: "dockerhub-secret",
    artifactName: "{artifact_name}",
    sonarKey: "{artifact_name}-test",
    solutionName: "PortalDocumentsReport.sln",
    sonarScannerDockerImage: "ghcr.io/nosinovacao/dotnet-sonar:24.01.5",
    deployJenkinsJobName: "{artifact_name}-deploy",
]
'''

def create_folder(session, folder_name, crumb_field, crumb_value, parent_path=None):
    folder_config = f"""<?xml version='1.1' encoding='UTF-8'?>
<com.cloudbees.hudson.plugins.folder.Folder plugin="cloudbees-folder@6.15">
  <description>Project folder for {folder_name}</description>
  <properties/>
</com.cloudbees.hudson.plugins.folder.Folder>"""

    headers = {
        "Content-Type": "application/xml",
        crumb_field: crumb_value
    }

    if parent_path:
        create_url = f"{JENKINS_URL}/job/{parent_path}/createItem?name={folder_name}&mode=com.cloudbees.hudson.plugins.folder.Folder"
    else:
        create_url = f"{JENKINS_URL}/createItem?name={folder_name}&mode=com.cloudbees.hudson.plugins.folder.Folder"
    response = session.post(create_url, data=folder_config, headers=headers)
    # 200: başarı, 400 veya 409: zaten var, devam et
    if response.status_code in [200, 400, 409]:
        return True
    return False

def create_pipeline_job(session, folder_path, job_name, crumb_field, crumb_value, domain, system_name=None, git_url=None, credentials_id=None, script_path=None):
    job_config = f"""<?xml version='1.1' encoding='UTF-8'?>
<flow-definition plugin="workflow-job@1289.vd1c337fd5354">
  <description>Pipeline job for {folder_path}</description>
  <keepDependencies>false</keepDependencies>
  <properties/>
  <definition class="org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition" plugin="workflow-cps@3697.vb_470e4543b_dc">
    <scm class="hudson.plugins.git.GitSCM" plugin="git@5.2.1">
      <configVersion>2</configVersion>
      <userRemoteConfigs>
        <hudson.plugins.git.UserRemoteConfig>
          <url>{git_url}</url>
          <credentialsId>{credentials_id}</credentialsId>
        </hudson.plugins.git.UserRemoteConfig>
      </userRemoteConfigs>
      <branches>
        <hudson.plugins.git.BranchSpec>
          <name>*/master</name>
        </hudson.plugins.git.BranchSpec>
      </branches>
      <doGenerateSubmoduleConfigurations>false</doGenerateSubmoduleConfigurations>
      <submoduleCfg class="list"/>
      <extensions/>
    </scm>
    <scriptPath>{script_path}</scriptPath>
    <lightweight>true</lightweight>
  </definition>
  <triggers/>
  <disabled>false</disabled>
</flow-definition>"""

    headers = {
        "Content-Type": "application/xml",
        crumb_field: crumb_value
    }

    create_url = f"{JENKINS_URL}/job/{folder_path}/createItem?name={job_name}"
    response = session.post(create_url, data=job_config, headers=headers)
    return response.status_code == 200

def create_deploy_jenkinsfile(allowed_branches, environment, domain, git_url, image_name):
    branches = [b.strip() for b in allowed_branches.split(',') if b.strip()]
    allowed_branches_str = ', '.join([f'"{b}"' for b in branches])
    docker_artifact_filter = '|'.join(branches)
    if environment == "prod":
        selected_branch = "master"
        repo_url = "https://bitbucket.org/atrosbt/yamls.git"
        deployed_cluster = domain.lower().replace(" ", "-")
    else:
        selected_branch = "deployments"
        repo_url = git_url
        deployed_cluster = "dev"  # Geliştirilebilir: test/staging için parametre alınabilir
    return f'''
def dockerArtifactFilter = "{docker_artifact_filter}"
def allowedBranches = [{allowed_branches_str}]
def environment = "{environment}"

def deployEnvs = [
    {branches[0]}: [
        git: [
            selectedBranch: "{selected_branch}",
            gitURL: "{repo_url}",
            credentialsId: "jenkins-bitbucket-cloud-ro-id",
        ],
        deploymentsFolderName: "argocd/{deployed_cluster}/{image_name}",
        chartYamlFileName: "Chart.yaml",
        valuesYamlFileName: "values.yaml",
        deployedClusterName: "{deployed_cluster}",
        helmBaseRepoChartFolder: false,
    ]
]
// ... pipeline stages burada ...
'''

def create_jenkins_job(project_name, use_argocd, domain, system_name=None, git_url=None, script_base_path=None):
    try:
        session = requests.Session()
        session.auth = HTTPBasicAuth(USERNAME, PASSWORD)
        crumb_url = f"{JENKINS_URL}/crumbIssuer/api/json"
        crumb_response = session.get(crumb_url)
        if crumb_response.status_code != 200:
            return False, f"Crumb alınamadı: {crumb_response.status_code}"
        crumb_data = crumb_response.json()
        crumb_field = crumb_data["crumbRequestField"]
        crumb_value = crumb_data["crumb"]

        folders = []
        if use_argocd == "evet":
            folders.append("ArgoCD")
        folders.append(domain)
        if system_name:
            folders.append(system_name)
        folders.append(project_name)

        parent_path = None
        for folder in folders:
            if not create_folder(session, folder, crumb_field, crumb_value, parent_path):
                if parent_path:
                    return False, f"Klasör oluşturulamadı: {parent_path}/job/{folder}"
                else:
                    return False, f"Klasör oluşturulamadı: {folder}"
            parent_path = folder if not parent_path else f"{parent_path}/job/{folder}"

        job_path = parent_path
        credentials_id = "jenkins-bitbucket-cloud-ro-id"
        # Build job
        build_script_path = f"{script_base_path}/build/Jenkinsfile"
        if not create_pipeline_job(session, job_path, "build", crumb_field, crumb_value, domain, system_name, git_url, credentials_id, build_script_path):
            return False, f"Build job'ı oluşturulamadı"
        # Deploy job
        deploy_script_path = f"{script_base_path}/deploy/Jenkinsfile"
        if not create_pipeline_job(session, job_path, "deploy", crumb_field, crumb_value, domain, system_name, git_url, credentials_id, deploy_script_path):
            return False, f"Deploy job'ı oluşturulamadı"
        return True, f"Proje '{project_name}' için build ve deploy job'ları başarıyla oluşturuldu!"
    except Exception as e:
        return False, str(e)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/create_job', methods=['POST'])
def create_job():
    project_name = request.form.get('project_name', '').strip()
    use_argocd = request.form.get('use_argocd', '')
    domain = request.form.get('domain', '')
    system_name = request.form.get('system_name', '').strip()
    filter_branch = request.form.get('filter_branch', '').strip()
    allowed_branches = request.form.get('allowed_branches', '').strip()
    image_name = request.form.get('image_name', '').strip()
    dockerfile_path = request.form.get('dockerfile_path', '').strip()
    git_url = request.form.get('git_url', '').strip()
    artifact_name = request.form.get('artifact_name', '').strip()
    environment = request.form.get('environment', '').strip()
    
    if not project_name:
        return jsonify({'success': False, 'message': 'Lütfen bir proje adı girin!'})
    if not use_argocd:
        return jsonify({'success': False, 'message': 'Lütfen ArgoCD seçeneğini belirleyin!'})
    if not domain:
        return jsonify({'success': False, 'message': 'Lütfen bir domain seçin!'})
    if not filter_branch or not allowed_branches or not image_name or not dockerfile_path or not git_url or not artifact_name or not environment:
        return jsonify({'success': False, 'message': 'Lütfen tüm build ve deploy parametrelerini doldurun!'})
    
    # allowed_branches'ı diziye çevir
    allowed_branches_list = [f'"{b.strip()}"' for b in allowed_branches.split(',') if b.strip()]
    allowed_branches_str = ','.join(allowed_branches_list)

    # Build Jenkinsfile içeriğini oluştur
    jenkinsfile_content = JENKINSFILE_TEMPLATE.format(
        filter_branch=filter_branch,
        allowed_branches=allowed_branches_str,
        image_name=image_name,
        dockerfile_path=dockerfile_path,
        git_url=git_url,
        artifact_name=artifact_name
    )
    build_jenkinsfile_path = f"jenkinsfiles/{domain}/{system_name}/{project_name}/build/Jenkinsfile"
    os.makedirs(os.path.dirname(build_jenkinsfile_path), exist_ok=True)
    with open(build_jenkinsfile_path, 'w', encoding='utf-8') as f:
        f.write(jenkinsfile_content)

    # Deploy Jenkinsfile içeriğini oluştur
    deploy_jenkinsfile_content = create_deploy_jenkinsfile(
        allowed_branches=allowed_branches,
        environment=environment,
        domain=domain,
        git_url=git_url,
        image_name=image_name
    )
    deploy_jenkinsfile_path = f"jenkinsfiles/{domain}/{system_name}/{project_name}/deploy/Jenkinsfile"
    os.makedirs(os.path.dirname(deploy_jenkinsfile_path), exist_ok=True)
    with open(deploy_jenkinsfile_path, 'w', encoding='utf-8') as f:
        f.write(deploy_jenkinsfile_content)

    success, message = create_jenkins_job(
        project_name, use_argocd, domain, system_name, git_url,
        script_base_path=f"jenkinsfiles/{domain}/{system_name}/{project_name}"
    )
    if success:
        message += f"<br>Build Jenkinsfile oluşturuldu: {build_jenkinsfile_path}"
        message += f"<br>Deploy Jenkinsfile oluşturuldu: {deploy_jenkinsfile_path}"
    return jsonify({'success': success, 'message': message})

if __name__ == "__main__":
    # templates klasörünü oluştur
    os.makedirs('templates', exist_ok=True)
    
    # HTML template'i oluştur
    with open('templates/index.html', 'w', encoding='utf-8') as f:
        f.write("""<!DOCTYPE html>
<html>
<head>
    <title>Jenkins Proje Oluşturucu</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            background-color: white;
            padding: 20px;
            border-radius: 5px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        h1 {
            color: #333;
            text-align: center;
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            color: #666;
        }
        input[type="text"], select {
            width: 100%;
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 4px;
            box-sizing: border-box;
        }
        button {
            background-color: #4CAF50;
            color: white;
            padding: 10px 15px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            width: 100%;
        }
        button:hover {
            background-color: #45a049;
        }
        #status {
            margin-top: 20px;
            padding: 10px;
            border-radius: 4px;
            display: none;
        }
        .success {
            background-color: #dff0d8;
            color: #3c763d;
        }
        .error {
            background-color: #f2dede;
            color: #a94442;
        }
        .info-box {
            background-color: #d9edf7;
            color: #31708f;
            padding: 15px;
            border-radius: 4px;
            margin-bottom: 20px;
        }
        .required::after {
            content: " *";
            color: red;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Jenkins Proje Oluşturucu</h1>
        <div class="info-box">
            Bu araç, Jenkins'te yeni bir proje klasör yapısı ve içinde build ve deploy pipeline job'ları oluşturur.
        </div>
        <div class="form-group">
            <label for="project_name" class="required">Proje Adı:</label>
            <input type="text" id="project_name" name="project_name" required>
        </div>
        <div class="form-group">
            <label for="use_argocd" class="required">ArgoCD Kullanılsın mı?</label>
            <select id="use_argocd" name="use_argocd" required>
                <option value="">Seçiniz...</option>
                <option value="evet">Evet</option>
                <option value="hayır">Hayır</option>
            </select>
        </div>
        <div class="form-group">
            <label for="domain" class="required">Domain:</label>
            <select id="domain" name="domain" required>
                <option value="">Seçiniz...</option>
                <option value="E-Mikro">E-Mikro</option>
                <option value="Mikro">Mikro</option>
                <option value="Mikrogrup">Mikrogrup</option>
                <option value="Parasut">Parasut</option>
                <option value="Shopside">Shopside</option>
                <option value="Zirve">Zirve</option>
            </select>
        </div>
        <div class="form-group">
            <label for="system_name">System Name (Opsiyonel):</label>
            <input type="text" id="system_name" name="system_name">
        </div>
        <div class="form-group">
            <label for="filter_branch" class="required">Filter Branch:</label>
            <input type="text" id="filter_branch" name="filter_branch" required>
        </div>
        <div class="form-group">
            <label for="allowed_branches" class="required">Allowed Branches:</label>
            <input type="text" id="allowed_branches" name="allowed_branches" required>
        </div>
        <div class="form-group">
            <label for="image_name" class="required">Image Name:</label>
            <input type="text" id="image_name" name="image_name" required>
        </div>
        <div class="form-group">
            <label for="dockerfile_path" class="required">Dockerfile Path:</label>
            <input type="text" id="dockerfile_path" name="dockerfile_path" required>
        </div>
        <div class="form-group">
            <label for="git_url" class="required">Git URL:</label>
            <input type="text" id="git_url" name="git_url" required>
        </div>
        <div class="form-group">
            <label for="artifact_name" class="required">Artifact Name:</label>
            <input type="text" id="artifact_name" name="artifact_name" required>
        </div>
        <div class="form-group">
            <label for="environment" class="required">Ortam:</label>
            <select id="environment" name="environment" required>
                <option value="">Seçiniz...</option>
                <option value="prod">Prod</option>
                <option value="nonprod">Nonprod</option>
            </select>
        </div>
        <button onclick="createProject()">Proje Oluştur</button>
        <div id="status"></div>
    </div>

    <script>
        function createProject() {
            const projectName = document.getElementById('project_name').value.trim();
            const useArgocd = document.getElementById('use_argocd').value;
            const domain = document.getElementById('domain').value;
            const systemName = document.getElementById('system_name').value.trim();
            const filterBranch = document.getElementById('filter_branch').value.trim();
            const allowedBranches = document.getElementById('allowed_branches').value.trim();
            const imageName = document.getElementById('image_name').value.trim();
            const dockerfilePath = document.getElementById('dockerfile_path').value.trim();
            const gitUrl = document.getElementById('git_url').value.trim();
            const artifactName = document.getElementById('artifact_name').value.trim();
            const environment = document.getElementById('environment').value.trim();
            const statusDiv = document.getElementById('status');
            
            if (!projectName) {
                showStatus('Lütfen bir proje adı girin!', false);
                return;
            }
            if (!useArgocd) {
                showStatus('Lütfen ArgoCD seçeneğini belirleyin!', false);
                return;
            }
            if (!domain) {
                showStatus('Lütfen bir domain seçin!', false);
                return;
            }
            if (!filterBranch || !allowedBranches || !imageName || !dockerfilePath || !gitUrl || !artifactName || !environment) {
                showStatus('Lütfen tüm build ve deploy parametrelerini doldurun!', false);
                return;
            }
            
            const formData = new FormData();
            formData.append('project_name', projectName);
            formData.append('use_argocd', useArgocd);
            formData.append('domain', domain);
            formData.append('system_name', systemName);
            formData.append('filter_branch', filterBranch);
            formData.append('allowed_branches', allowedBranches);
            formData.append('image_name', imageName);
            formData.append('dockerfile_path', dockerfilePath);
            formData.append('git_url', gitUrl);
            formData.append('artifact_name', artifactName);
            formData.append('environment', environment);
            
            fetch('/create_job', {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                showStatus(data.message, data.success);
            })
            .catch(error => {
                showStatus('Bir hata oluştu: ' + error, false);
            });
        }
        
        function showStatus(message, success) {
            const statusDiv = document.getElementById('status');
            statusDiv.textContent = message;
            statusDiv.style.display = 'block';
            statusDiv.className = success ? 'success' : 'error';
        }
    </script>
</body>
</html>""")
    
    app.run(host='0.0.0.0', port=3000, debug=True)