# FaceTrace — install, AWS bootstrap, health check, or launch app.
#   .\setup.ps1                    setup only what is missing
#   .\setup.ps1 -SkipAws           Python deps only
#   .\setup.ps1 -RedeployLambdas   force-update Lambda code even if they exist
#   .\setup.ps1 -Check             verify everything works
#   .\setup.ps1 -Run               start the app
param(
    [switch]$SkipAws,
    [switch]$SkipLambdas,
    [switch]$RedeployLambdas,
    [switch]$Check,
    [switch]$Run,
    [switch]$Destroy
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

# AWS resource names (must match app.py and lambdas/)
$AwsRegion       = "ap-south-1"
$BucketName      = "video-detection-system"
$QueueName       = "LensJobQueue"
$CollectionId    = "OrgFaces"
$JobTable        = "LensJobStatus"
$ResultsTable    = "LensResults"
$SnsTopicName    = "LensRekognitionComplete"
$RekognitionRole = "LensRekognitionSNSRole"
$StarterLambda   = "P2_Starter"
$FetcherLambda   = "P2_Fetcher"
$StarterRole     = "P2-Starter-LambdaRole"
$FetcherRole     = "P2-Fetcher-LambdaRole"

$script:Created = 0
$script:Skipped = 0

function Write-Step($msg) {
    Write-Host "`n========================================" -ForegroundColor Magenta
    Write-Host " $msg" -ForegroundColor Magenta
    Write-Host "========================================" -ForegroundColor Magenta
}
function Write-Sub($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    OK: $msg" -ForegroundColor Green; $script:Created++ }
function Write-Skip($msg) { Write-Host "    SKIP: $msg" -ForegroundColor DarkYellow; $script:Skipped++ }
function Write-Warn($msg) { Write-Host "    WARN: $msg" -ForegroundColor Yellow }

function Show-NamingWarning {
    Write-Host ""
    Write-Host "  IMPORTANT - shared resource names" -ForegroundColor Yellow
    Write-Host "  This project uses fixed AWS names in region $AwsRegion :" -ForegroundColor Yellow
    Write-Host "    S3: $BucketName" -ForegroundColor DarkYellow
    Write-Host "    DynamoDB: $JobTable, $ResultsTable" -ForegroundColor DarkYellow
    Write-Host "    SQS: $QueueName  |  SNS: $SnsTopicName" -ForegroundColor DarkYellow
    Write-Host "    Rekognition: $CollectionId" -ForegroundColor DarkYellow
    Write-Host "    Lambdas: $StarterLambda, $FetcherLambda" -ForegroundColor DarkYellow
    Write-Host "    IAM roles: $RekognitionRole, $StarterRole, $FetcherRole" -ForegroundColor DarkYellow
    Write-Host ""
    Write-Host "  If another app already uses these names, setup may connect to" -ForegroundColor Yellow
    Write-Host "  that app's resources or fail. Use a dedicated AWS account or" -ForegroundColor Yellow
    Write-Host "  rename resources in app.py / lambdas/ before running setup." -ForegroundColor Yellow
    Write-Host ""
}

function Safe-AwsCmd($block) {
    $oldEAP = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        & $block 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $oldEAP
    }
}

function Test-S3Bucket {
    return (Safe-AwsCmd { aws s3api head-bucket --bucket $BucketName })
}

function Test-DynamoTable($name) {
    $tables = (aws dynamodb list-tables --region $AwsRegion --output json | ConvertFrom-Json).TableNames
    return ($name -in $tables)
}

function Test-SqsQueue {
    return (Safe-AwsCmd { aws sqs get-queue-url --region $AwsRegion --queue-name $QueueName })
}

function Test-SnsTopic($accountId) {
    $arn = "arn:aws:sns:${AwsRegion}:${accountId}:${SnsTopicName}"
    $topics = (aws sns list-topics --region $AwsRegion --output json | ConvertFrom-Json).Topics.TopicArn
    return ($arn -in $topics)
}

function Test-RekognitionCollection {
    return (Safe-AwsCmd { aws rekognition describe-collection --region $AwsRegion --collection-id $CollectionId })
}

function Test-IamRole($name) {
    return (Safe-AwsCmd { aws iam get-role --role-name $name })
}

function Test-Lambda($name) {
    return (Safe-AwsCmd { aws lambda get-function --region $AwsRegion --function-name $name })
}

function Show-SetupPlan($accountId) {
    Write-Sub "Scanning what already exists"
    $rows = @(
        @{ Name = "S3 bucket $BucketName";           Exists = (Test-S3Bucket) },
        @{ Name = "DynamoDB $JobTable";              Exists = (Test-DynamoTable $JobTable) },
        @{ Name = "DynamoDB $ResultsTable";          Exists = (Test-DynamoTable $ResultsTable) },
        @{ Name = "SQS $QueueName";                  Exists = (Test-SqsQueue) },
        @{ Name = "SNS $SnsTopicName";               Exists = (Test-SnsTopic $accountId) },
        @{ Name = "Rekognition $CollectionId";       Exists = (Test-RekognitionCollection) },
        @{ Name = "IAM $RekognitionRole";            Exists = (Test-IamRole $RekognitionRole) },
        @{ Name = "IAM $StarterRole";                Exists = (Test-IamRole $StarterRole) },
        @{ Name = "IAM $FetcherRole";                Exists = (Test-IamRole $FetcherRole) },
        @{ Name = "Lambda $StarterLambda";           Exists = (Test-Lambda $StarterLambda) },
        @{ Name = "Lambda $FetcherLambda";           Exists = (Test-Lambda $FetcherLambda) },
        @{ Name = "Local .venv";                     Exists = (Test-Path (Join-Path $Root ".venv")) }
    )
    foreach ($r in $rows) {
        $tag = if ($r.Exists) { "exists - will skip" } else { "missing - will create" }
        $color = if ($r.Exists) { "DarkYellow" } else { "Green" }
        Write-Host ("    {0,-40} {1}" -f $r.Name, $tag) -ForegroundColor $color
    }
    Write-Host ""
    Write-Host "  Only missing pieces will be created. Re-run anytime safely." -ForegroundColor DarkGray
}

# ── Health check ──────────────────────────────────────────────────────────────
function Invoke-HealthCheck {
    $region = $AwsRegion
    $ok = 0; $fail = 0
    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"

    function Test-H($label, $block) {
        try { & $block; Write-Host "[OK]  $label" -ForegroundColor Green; $script:ok++ }
        catch { Write-Host "[FAIL] $label - $_" -ForegroundColor Red; $script:fail++ }
    }

    Write-Step "Health Check"
    Test-H "AWS credentials" { aws sts get-caller-identity | Out-Null; if ($LASTEXITCODE -ne 0) { throw "not configured" } }
    Test-H "S3 bucket" { if (-not (Test-S3Bucket)) { throw "missing" } }
    Test-H "DynamoDB tables" {
        if (-not (Test-DynamoTable $JobTable) -or -not (Test-DynamoTable $ResultsTable)) { throw "missing" }
    }
    Test-H "SQS queue" { if (-not (Test-SqsQueue)) { throw "missing" } }
    Test-H "Rekognition collection" { if (-not (Test-RekognitionCollection)) { throw "missing" } }
    Test-H "Lambda $StarterLambda" { if (-not (Test-Lambda $StarterLambda)) { throw "missing" } }
    Test-H "Lambda $FetcherLambda" { if (-not (Test-Lambda $FetcherLambda)) { throw "missing" } }
    Test-H "Local .venv" { if (-not (Test-Path $venvPy)) { throw "run .\setup.ps1 first" } }
    Test-H "Python imports" { & $venvPy -c "import boto3, streamlit" | Out-Null }

    $color = if ($fail -eq 0) { "Green" } else { "Yellow" }
    Write-Host "`n$ok passed, $fail failed" -ForegroundColor $color
    if ($fail -eq 0) { Write-Host "Run: .\setup.ps1 -Run" -ForegroundColor Cyan }
    exit $(if ($fail -gt 0) { 1 } else { 0 })
}

# ── Python env ────────────────────────────────────────────────────────────────
function Invoke-SetupPython {
    $venv = Join-Path $Root ".venv"
    $venvPy = Join-Path $venv "Scripts\python.exe"

    if (Test-Path $venvPy) {
        Write-Skip ".venv already exists"
    } else {
        python -m venv $venv
        Write-Ok "Created .venv"
    }

    & $venvPy -c "import streamlit, boto3" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Skip "Python packages already installed"
    } else {
        & $venvPy -m pip install -q -r (Join-Path $Root "requirements.txt")
        Write-Ok "Installed Python packages"
    }
}

# ── AWS provision ─────────────────────────────────────────────────────────────
function Invoke-ProvisionAws {
    $identity = aws sts get-caller-identity --output json | ConvertFrom-Json
    $accountId = $identity.Account
    Write-Ok "Authenticated as $($identity.Arn)"

    Show-SetupPlan $accountId

    Write-Sub "S3 bucket: $BucketName"
    if (Test-S3Bucket) {
        Write-Skip "Bucket already exists"
        if ($identity.Account -ne "760402325768") {
            Write-Warn "Bucket exists in your account - confirm it belongs to FaceTrace, not another app"
        }
    } else {
        aws s3api create-bucket --bucket $BucketName --region $AwsRegion `
            --create-bucket-configuration LocationConstraint=$AwsRegion 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $err = aws s3api create-bucket --bucket $BucketName --region $AwsRegion 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-Warn "Could not create bucket - name may be taken globally by another AWS account"
                throw $err
            }
        }
        Write-Ok "Created bucket"
    }

    Write-Sub "DynamoDB tables"
    if (Test-DynamoTable $JobTable) {
        Write-Skip "$JobTable already exists"
        Write-Warn "If another app owns this table, FaceTrace data may mix with it"
    } else {
        aws dynamodb create-table --region $AwsRegion --table-name $JobTable `
            --attribute-definitions AttributeName=video_id,AttributeType=S `
            --key-schema AttributeName=video_id,KeyType=HASH --billing-mode PAY_PER_REQUEST | Out-Null
        Write-Ok "Created $JobTable"
    }
    if (Test-DynamoTable $ResultsTable) {
        Write-Skip "$ResultsTable already exists"
    } else {
        aws dynamodb create-table --region $AwsRegion --table-name $ResultsTable `
            --attribute-definitions AttributeName=person,AttributeType=S AttributeName=video_timestamp,AttributeType=S `
            --key-schema AttributeName=person,KeyType=HASH AttributeName=video_timestamp,KeyType=RANGE `
            --billing-mode PAY_PER_REQUEST | Out-Null
        Write-Ok "Created $ResultsTable"
    }
    if (Test-DynamoTable $JobTable) { aws dynamodb wait table-exists --region $AwsRegion --table-name $JobTable }
    if (Test-DynamoTable $ResultsTable) { aws dynamodb wait table-exists --region $AwsRegion --table-name $ResultsTable }

    Write-Sub "SQS queue: $QueueName"
    if (Test-SqsQueue) {
        Write-Skip "Queue already exists"
    } else {
        aws sqs create-queue --region $AwsRegion --queue-name $QueueName `
            --attributes VisibilityTimeout=360,MessageRetentionPeriod=86400 | Out-Null
        Write-Ok "Created queue"
    }
    $queueUrl = aws sqs get-queue-url --region $AwsRegion --queue-name $QueueName --output text
    $queueArn = aws sqs get-queue-attributes --region $AwsRegion --queue-url $queueUrl `
        --attribute-names QueueArn --query Attributes.QueueArn --output text

    Write-Sub "SNS topic: $SnsTopicName"
    $topicArn = "arn:aws:sns:${AwsRegion}:${accountId}:${SnsTopicName}"
    if (Test-SnsTopic $accountId) {
        Write-Skip "Topic already exists"
    } else {
        $topicArn = aws sns create-topic --region $AwsRegion --name $SnsTopicName --output text
        Write-Ok "Created topic"
    }

    Write-Sub "IAM roles"
    $rekTrust  = '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"rekognition.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
    $rekPolicy = "{`"Version`":`"2012-10-17`",`"Statement`":[{`"Effect`":`"Allow`",`"Action`":`"sns:Publish`",`"Resource`":`"$topicArn`"}]}"
    $t1 = "$env:TEMP\rek-trust.json"; $p1 = "$env:TEMP\rek-policy.json"
    $rekTrust | Out-File $t1 -Encoding ascii; $rekPolicy | Out-File $p1 -Encoding ascii
    if (Test-IamRole $RekognitionRole) {
        Write-Skip "$RekognitionRole already exists"
    } else {
        aws iam create-role --role-name $RekognitionRole --assume-role-policy-document "file://$t1" | Out-Null
        Write-Ok "Created $RekognitionRole"
    }
    aws iam put-role-policy --role-name $RekognitionRole --policy-name RekognitionSNSPublish --policy-document "file://$p1" | Out-Null
    Remove-Item $t1, $p1 -ErrorAction SilentlyContinue

    function Ensure-Role($roleName, $policyName, $policyJson) {
        $trust = '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
        $tf = "$env:TEMP\$roleName-trust.json"; $pf = "$env:TEMP\$roleName-policy.json"
        $trust | Out-File $tf -Encoding ascii; $policyJson | Out-File $pf -Encoding ascii
        if (Test-IamRole $roleName) {
            Write-Skip "IAM role $roleName already exists"
        } else {
            aws iam create-role --role-name $roleName --assume-role-policy-document "file://$tf" | Out-Null
            Write-Ok "Created IAM role $roleName"
        }
        aws iam put-role-policy --role-name $roleName --policy-name $policyName --policy-document "file://$pf" | Out-Null
        Remove-Item $tf, $pf -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        return "arn:aws:iam::${accountId}:role/$roleName"
    }

    $starterPolicy = @"
{"Version":"2012-10-17","Statement":[
{"Effect":"Allow","Action":["logs:*"],"Resource":"arn:aws:logs:*:*:*"},
{"Effect":"Allow","Action":["sqs:ReceiveMessage","sqs:DeleteMessage","sqs:GetQueueAttributes"],"Resource":"$queueArn"},
{"Effect":"Allow","Action":"rekognition:StartFaceSearch","Resource":"*"},
{"Effect":"Allow","Action":["dynamodb:PutItem","dynamodb:UpdateItem"],"Resource":"arn:aws:dynamodb:${AwsRegion}:${accountId}:table/${JobTable}"},
{"Effect":"Allow","Action":"iam:PassRole","Resource":"arn:aws:iam::${accountId}:role/${RekognitionRole}"},
{"Effect":"Allow","Action":["s3:GetObject"],"Resource":"arn:aws:s3:::${BucketName}/*"}
]}
"@
    $fetcherPolicy = @"
{"Version":"2012-10-17","Statement":[
{"Effect":"Allow","Action":["logs:*"],"Resource":"arn:aws:logs:*:*:*"},
{"Effect":"Allow","Action":"rekognition:GetFaceSearch","Resource":"*"},
{"Effect":"Allow","Action":["dynamodb:GetItem","dynamodb:PutItem","dynamodb:UpdateItem"],"Resource":["arn:aws:dynamodb:${AwsRegion}:${accountId}:table/${JobTable}","arn:aws:dynamodb:${AwsRegion}:${accountId}:table/${ResultsTable}"]},
{"Effect":"Allow","Action":["s3:GetObject","s3:PutObject"],"Resource":"arn:aws:s3:::${BucketName}/*"}
]}
"@
    $starterRoleArn = Ensure-Role $StarterRole "P2StarterPolicy" $starterPolicy
    $fetcherRoleArn = Ensure-Role $FetcherRole "P2FetcherPolicy" $fetcherPolicy

    Write-Sub "Rekognition collection: $CollectionId"
    if (Test-RekognitionCollection) {
        Write-Skip "Collection already exists"
        Write-Warn "Existing faces in this collection will be used - may belong to another project"
    } else {
        aws rekognition create-collection --region $AwsRegion --collection-id $CollectionId | Out-Null
        Write-Ok "Created collection"
    }

    return @{
        accountId      = $accountId
        queueArn       = $queueArn
        topicArn       = $topicArn
        starterRoleArn = $starterRoleArn
        fetcherRoleArn = $fetcherRoleArn
    }
}

# ── Lambda deploy ─────────────────────────────────────────────────────────────
function Invoke-DeployLambdas($awsEnv) {
    $needStarter = -not (Test-Lambda $StarterLambda)
    $needFetcher = -not (Test-Lambda $FetcherLambda)
    $needDeploy  = $needStarter -or $needFetcher -or $RedeployLambdas

    if (-not $needDeploy) {
        Write-Skip "Both Lambdas already exist (use -RedeployLambdas to force code update)"
    } else {
        $buildRoot = Join-Path $Root "build"
        $starterZip = Join-Path $buildRoot "starter.zip"
        $fetcherZip = Join-Path $buildRoot "fetcher.zip"

        Write-Sub "Building Lambda packages"
        if (Test-Path $buildRoot) { Remove-Item $buildRoot -Recurse -Force }
        New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null

        $starterDir = Join-Path $buildRoot "starter"
        New-Item -ItemType Directory -Path $starterDir -Force | Out-Null
        Copy-Item (Join-Path $Root "lambdas\P2_Starter.py") (Join-Path $starterDir "lambda_function.py")
        Compress-Archive -Path "$starterDir\*" -DestinationPath $starterZip

        $fetcherDir = Join-Path $buildRoot "fetcher"
        New-Item -ItemType Directory -Path $fetcherDir -Force | Out-Null
        Copy-Item (Join-Path $Root "lambdas\P2_Fetcher.py") (Join-Path $fetcherDir "lambda_function.py")
        $oldEAP = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        try {
            python -m pip install opencv-python-headless numpy -t $fetcherDir `
                --platform manylinux2014_x86_64 --python-version 3.12 --only-binary=:all: --quiet 2>&1 | Out-Null
            if (-not (Test-Path (Join-Path $fetcherDir "cv2"))) {
                python -m pip install opencv-python-headless numpy -t $fetcherDir --quiet 2>&1 | Out-Null
            }
        } finally {
            $ErrorActionPreference = $oldEAP
        }
        python -c "import shutil; shutil.make_archive(r'$($fetcherZip -replace '\.zip$','')', 'zip', r'$fetcherDir')"
        Write-Ok "Built Lambda zip packages"

        function Deploy-Fn($name, $zip, $roleArn, $mem) {
            $sizeMB = [math]::Round((Get-Item $zip).Length / 1MB, 1)
            $useS3  = $sizeMB -gt 50

            if ($useS3) {
                $s3Key = "lambda-deploy/$name.zip"
                Write-Host "    (zip is ${sizeMB}MB - uploading via S3)" -ForegroundColor DarkGray
                aws s3 cp $zip "s3://$BucketName/$s3Key" --region $AwsRegion | Out-Null
                $s3Loc = @{ S3Bucket = $BucketName; S3Key = $s3Key }
            }

            if (Test-Lambda $name) {
                if ($RedeployLambdas) {
                    if ($useS3) {
                        aws lambda update-function-code --region $AwsRegion --function-name $name `
                            --s3-bucket $BucketName --s3-key "lambda-deploy/$name.zip" | Out-Null
                    } else {
                        aws lambda update-function-code --region $AwsRegion --function-name $name `
                            --zip-file "fileb://$zip" | Out-Null
                    }
                    Start-Sleep -Seconds 5
                    aws lambda update-function-configuration --region $AwsRegion --function-name $name `
                        --timeout 300 --memory-size $mem --runtime python3.12 | Out-Null
                    Write-Ok "Updated $name"
                }
            } else {
                if ($useS3) {
                    aws lambda create-function --region $AwsRegion --function-name $name --runtime python3.12 `
                        --role $roleArn --handler lambda_function.lambda_handler `
                        --code "S3Bucket=$BucketName,S3Key=lambda-deploy/$name.zip" `
                        --timeout 300 --memory-size $mem | Out-Null
                } else {
                    aws lambda create-function --region $AwsRegion --function-name $name --runtime python3.12 `
                        --role $roleArn --handler lambda_function.lambda_handler --zip-file "fileb://$zip" `
                        --timeout 300 --memory-size $mem | Out-Null
                }
                Write-Ok "Created $name"
                Start-Sleep -Seconds 10
            }
        }

        Write-Sub "Deploying Lambdas"
        if ($needStarter -or $RedeployLambdas) { Deploy-Fn $StarterLambda $starterZip $awsEnv.starterRoleArn 256 }
        else { Write-Skip "$StarterLambda already deployed" }
        if ($needFetcher -or $RedeployLambdas) { Deploy-Fn $FetcherLambda $fetcherZip $awsEnv.fetcherRoleArn 1024 }
        else { Write-Skip "$FetcherLambda already deployed" }
    }

    Write-Sub "Lambda triggers"
    $mappings = aws lambda list-event-source-mappings --region $AwsRegion --function-name $StarterLambda --output json | ConvertFrom-Json
    if ($mappings.EventSourceMappings | Where-Object { $_.EventSourceArn -eq $awsEnv.queueArn }) {
        Write-Skip "SQS trigger already attached to $StarterLambda"
    } else {
        $ok = Safe-AwsCmd { aws lambda create-event-source-mapping --region $AwsRegion --function-name $StarterLambda `
            --event-source-arn $awsEnv.queueArn --batch-size 1 --enabled }
        if ($ok) { Write-Ok "Attached SQS trigger to $StarterLambda" }
        else { Write-Warn "Failed to attach SQS trigger to $StarterLambda" }
    }

    $fetcherArn = "arn:aws:lambda:${AwsRegion}:$($awsEnv.accountId):function:$FetcherLambda"
    $subs = aws sns list-subscriptions-by-topic --region $AwsRegion --topic-arn $awsEnv.topicArn --output json | ConvertFrom-Json
    if ($subs.Subscriptions | Where-Object { $_.Endpoint -eq $fetcherArn }) {
        Write-Skip "SNS trigger already attached to $FetcherLambda"
    } else {
        Safe-AwsCmd { aws sns subscribe --region $AwsRegion --topic-arn $awsEnv.topicArn --protocol lambda --notification-endpoint $fetcherArn } | Out-Null
        Write-Ok "Attached SNS subscription to $FetcherLambda"
    }
    # Always ensure the Lambda resource policy allows SNS to invoke it (idempotent - safe to re-run)
    # First remove any stale/duplicate statement, then re-add cleanly
    Safe-AwsCmd { aws lambda remove-permission --region $AwsRegion --function-name $FetcherLambda --statement-id "sns-invoke-fetcher" } | Out-Null
    $permOk = Safe-AwsCmd { aws lambda add-permission --region $AwsRegion --function-name $FetcherLambda `
        --statement-id "sns-invoke-fetcher" --action lambda:InvokeFunction --principal sns.amazonaws.com `
        --source-arn $awsEnv.topicArn }
    if ($permOk) { Write-Ok "Lambda resource policy grants SNS invoke on $FetcherLambda" }
    else { Write-Warn "Could not set Lambda resource policy for $FetcherLambda" }
}

# ── Launch app ────────────────────────────────────────────────────────────────
function Invoke-RunApp {
    $streamlit = Join-Path $Root ".venv\Scripts\streamlit.exe"
    if (-not (Test-Path $streamlit)) { Write-Error "No .venv - run .\setup.ps1 first" }
    Write-Host "Starting FaceTrace at http://localhost:8501" -ForegroundColor Green
    Set-Location $Root
    & $streamlit run app.py
}

# ── Teardown ──────────────────────────────────────────────────────────────────
function Invoke-Teardown {
    Write-Step "FaceTrace Teardown (Destroying AWS Resources)"
    
    # 1. Lambda Functions
    Write-Sub "Deleting Lambda functions"
    if (Test-Lambda $StarterLambda) {
        try {
            aws lambda delete-function --region $AwsRegion --function-name $StarterLambda | Out-Null
            Write-Ok "Deleted Lambda $StarterLambda"
        } catch { Write-Warn "Failed to delete Lambda ${StarterLambda}: $_" }
    } else { Write-Skip "Lambda $StarterLambda does not exist" }

    if (Test-Lambda $FetcherLambda) {
        try {
            aws lambda delete-function --region $AwsRegion --function-name $FetcherLambda | Out-Null
            Write-Ok "Deleted Lambda $FetcherLambda"
        } catch { Write-Warn "Failed to delete Lambda ${FetcherLambda}: $_" }
    } else { Write-Skip "Lambda $FetcherLambda does not exist" }

    # 2. IAM Roles & Policies
    Write-Sub "Deleting IAM roles"
    function Safe-DeleteRole($roleName) {
        if (-not (Test-IamRole $roleName)) {
            Write-Skip "IAM role $roleName does not exist"
            return
        }
        try {
            # List and delete all inline policies
            $inlinePolicies = (aws iam list-role-policies --role-name $roleName --output json | ConvertFrom-Json).PolicyNames
            foreach ($p in $inlinePolicies) {
                aws iam delete-role-policy --role-name $roleName --policy-name $p | Out-Null
            }
            
            # List and detach all attached managed policies
            $attachedPolicies = (aws iam list-attached-role-policies --role-name $roleName --output json | ConvertFrom-Json).AttachedPolicies
            foreach ($p in $attachedPolicies) {
                aws iam detach-role-policy --role-name $roleName --policy-arn $p.PolicyArn | Out-Null
            }
            
            # Delete the role
            aws iam delete-role --role-name $roleName | Out-Null
            Write-Ok "Deleted IAM role $roleName"
        } catch {
            Write-Warn "Failed to delete IAM role $roleName - $_"
        }
    }

    # Safe-DeleteRole $RekognitionRole
    # Safe-DeleteRole $StarterRole
    # Safe-DeleteRole $FetcherRole

    # 3. SNS Topic
    Write-Sub "Deleting SNS topic: $SnsTopicName"
    try {
        $identity = aws sts get-caller-identity --output json | ConvertFrom-Json
        $accountId = $identity.Account
        if (Test-SnsTopic $accountId) {
            $topicArn = "arn:aws:sns:${AwsRegion}:${accountId}:${SnsTopicName}"
            aws sns delete-topic --region $AwsRegion --topic-arn $topicArn | Out-Null
            Write-Ok "Deleted SNS topic"
        } else { Write-Skip "SNS topic does not exist" }
    } catch { Write-Warn "Failed to delete SNS topic: $_" }

    # 4. SQS Queue
    Write-Sub "Deleting SQS queue: $QueueName"
    if (Test-SqsQueue) {
        try {
            $queueUrl = aws sqs get-queue-url --region $AwsRegion --queue-name $QueueName --output text
            aws sqs delete-queue --region $AwsRegion --queue-url $queueUrl | Out-Null
            Write-Ok "Deleted SQS queue"
        } catch { Write-Warn "Failed to delete SQS queue: $_" }
    } else { Write-Skip "SQS queue does not exist" }

    # 5. DynamoDB Tables
    Write-Sub "Deleting DynamoDB tables"
    foreach ($table in @($JobTable, $ResultsTable)) {
        if (Test-DynamoTable $table) {
            try {
                aws dynamodb delete-table --region $AwsRegion --table-name $table | Out-Null
                Write-Ok "Deleted DynamoDB table $table"
            } catch { Write-Warn "Failed to delete DynamoDB table ${table}: $_" }
        } else { Write-Skip "DynamoDB table $table does not exist" }
    }

    # 6. Rekognition Collection
    Write-Sub "Deleting Rekognition collection: $CollectionId"
    if (Test-RekognitionCollection) {
        try {
            aws rekognition delete-collection --region $AwsRegion --collection-id $CollectionId | Out-Null
            Write-Ok "Deleted Rekognition collection"
        } catch { Write-Warn "Failed to delete Rekognition collection: $_" }
    } else { Write-Skip "Rekognition collection does not exist" }

    # 7. S3 Bucket
    Write-Sub "Deleting S3 bucket: $BucketName"
    if (Test-S3Bucket) {
        try {
            Write-Host "    Emptying S3 bucket objects..." -ForegroundColor DarkYellow
            aws s3 rm "s3://$BucketName" --recursive | Out-Null
            aws s3api delete-bucket --bucket $BucketName --region $AwsRegion | Out-Null
            Write-Ok "Deleted S3 bucket"
        } catch { Write-Warn "Failed to delete S3 bucket: $_" }
    } else { Write-Skip "S3 bucket does not exist" }

    # Local build artifacts
    $buildRoot = Join-Path $Root "build"
    if (Test-Path $buildRoot) {
        Remove-Item $buildRoot -Recurse -Force -ErrorAction SilentlyContinue
        Write-Ok "Deleted local build folder"
    }

    Write-Step "Teardown Complete"
}

# ── Main ──────────────────────────────────────────────────────────────────────
if ($Destroy) {
    Invoke-Teardown
    exit 0
}

if ($Check) { Invoke-HealthCheck }

if (-not $Check -and -not $Run -and -not $Destroy) {
    Write-Step "FaceTrace Setup"
    Show-NamingWarning

    if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Write-Error "Install Python 3.10+" }
    if (-not $SkipAws -and -not (Get-Command aws -ErrorAction SilentlyContinue)) { Write-Error "Install AWS CLI v2" }
    if (-not $SkipAws) {
        aws sts get-caller-identity | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Error "Run aws configure first" }
    }

    Write-Step "Python environment"
    Invoke-SetupPython

    if (-not $SkipAws) {
        Write-Step "AWS infrastructure"
        $awsEnv = Invoke-ProvisionAws
        if (-not $SkipLambdas) {
            Write-Step "Lambda deployment"
            Invoke-DeployLambdas $awsEnv
        }
    }

    Write-Step "Done"
    Write-Host "  Created: $script:Created   Skipped (already existed): $script:Skipped" -ForegroundColor DarkGray
    Write-Host "  Start app: .\setup.ps1 -Run" -ForegroundColor Cyan
}

if ($Run) { Invoke-RunApp }
