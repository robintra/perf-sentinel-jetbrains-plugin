ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PYTHON ?= python3
GRADLE ?= $(ROOT)/gradlew
DOTNET ?= dotnet
QODANA ?= qodana
OSV_SCANNER ?= osv-scanner
GITLEAKS ?= gitleaks
ACTIONLINT ?= actionlint
ZIZMOR ?= zizmor

RAW_VERSION := $(value VERSION)
VERSION_REMAINDER := $(subst 9,,$(subst 8,,$(subst 7,,$(subst 6,,$(subst 5,,$(subst 4,,$(subst 3,,$(subst 2,,$(subst 1,,$(subst 0,,$(subst .,,$(RAW_VERSION))))))))))))
ifneq ($(filter release-check,$(MAKECMDGOALS)),)
ifneq ($(strip $(VERSION_REMAINDER)),)
$(error VERSION must be an exact stable semantic version)
endif
endif

GRADLE_FLAGS := --no-daemon --dependency-verification strict
LANGUAGE_TESTS := testPyCharm253 testPhpStorm253 testRustRover253 testRustRover262 testRubyMine253 testWebStorm253 testGoLand253
PLUGIN_VERSION ?= 0.1.0-SNAPSHOT
PLUGIN_ZIP ?= $(ROOT)/build/distributions/perf-sentinel-$(PLUGIN_VERSION).zip
RIDER_PROJECT := src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj
NUGET_CONFIG := src/dotnet/NuGet.Config
LOCK_INPUTS := gradle.lockfile protocol/gradle.lockfile rider-frontend/gradle.lockfile gradle/verification-metadata.xml src/dotnet/PerfSentinel.Rider/packages.lock.json src/dotnet/PerfSentinel.Rider.Tests/packages.lock.json

.PHONY: check-disk check-locks verify-fast verify security release-check

check-disk:
	@$(PYTHON) -c 'import shutil,sys; free=shutil.disk_usage(sys.argv[1]).free; print(f"Disk guard: {free // (1024**3)} GiB free"); sys.exit(0 if free >= 25 * 1024**3 else "At least 25 GiB free are required")' "$(ROOT)"

check-locks:
	@cd "$(ROOT)" && git diff HEAD --exit-code -- $(LOCK_INPUTS)

verify-fast: check-disk
	@if [ "$(OS)" != "Windows_NT" ]; then echo "Rider verification requires Windows; pending_windows remains blocking." >&2; exit 1; fi
	@cd "$(ROOT)" && $(PYTHON) -B -m unittest discover -s scripts/tests -p 'test_*.py'
	@cd "$(ROOT)" && $(GRADLE) $(GRADLE_FLAGS) compileKotlin :rider-frontend:compileKotlin test $(LANGUAGE_TESTS) koverXmlReport buildPlugin
	@cd "$(ROOT)" && $(PYTHON) scripts/inspect-plugin-zip.py "$(PLUGIN_ZIP)"
	@cd "$(ROOT)" && $(DOTNET) restore "$(RIDER_PROJECT)" --locked-mode --configfile "$(NUGET_CONFIG)"
	@cd "$(ROOT)" && $(DOTNET) test "$(RIDER_PROJECT)" --configuration Release --no-restore --settings src/dotnet/coverage.runsettings --results-directory build/dotnet/TestResults --collect:"XPlat Code Coverage" --logger trx

verify: check-locks verify-fast
	@cd "$(ROOT)" && $(PYTHON) scripts/check-supply-chain.py
	@cd "$(ROOT)" && $(PYTHON) scripts/check-analysis-config.py
	@$(MAKE) --no-print-directory check-disk
	@cd "$(ROOT)" && $(GRADLE) $(GRADLE_FLAGS) verifyPluginProjectConfiguration verifyPlugin buildPlugin qodanaScan
ifeq ($(OS),Windows_NT)
	@cd "$(ROOT)" && $(QODANA) scan --config qodana-dotnet.yml --results-dir build/qodana-rider/results
else
	@echo "Native Rider Qodana requires trusted Windows execution." >&2; exit 1
endif
	@cd "$(ROOT)" && $(PYTHON) scripts/inspect-plugin-zip.py "$(PLUGIN_ZIP)"
	@$(MAKE) --no-print-directory check-locks

security: check-disk
	@cd "$(ROOT)" && $(OSV_SCANNER) scan source --recursive --licenses='Apache-2.0,Apache-2.0 WITH LLVM-exception,BSD-2-Clause,BSD-3-Clause,CDDL-1.1,EPL-1.0,EPL-2.0,ISC,MIT,MPL-2.0,Unicode-3.0,Zlib' .
	@cd "$(ROOT)" && $(PYTHON) scripts/check-supply-chain.py
	@cd "$(ROOT)" && $(PYTHON) scripts/check-analysis-config.py
	@cd "$(ROOT)" && $(GRADLE) $(GRADLE_FLAGS) dependencies :protocol:dependencies :rider-frontend:dependencies --configuration runtimeClasspath
	@cd "$(ROOT)" && $(DOTNET) restore src/dotnet/PerfSentinel.Rider/PerfSentinel.Rider.csproj --locked-mode --configfile "$(NUGET_CONFIG)" -p:NuGetAudit=true -p:NuGetAuditMode=all -p:TreatWarningsAsErrors=true
	@cd "$(ROOT)" && $(DOTNET) restore "$(RIDER_PROJECT)" --locked-mode --configfile "$(NUGET_CONFIG)" -p:NuGetAudit=true -p:NuGetAuditMode=all -p:TreatWarningsAsErrors=true
	@cd "$(ROOT)" && $(GITLEAKS) git --redact --no-banner --exit-code=1
	@if find "$(ROOT)/.github/workflows" -type f \( -name '*.yml' -o -name '*.yaml' \) -print -quit 2>/dev/null | grep -q .; then cd "$(ROOT)" && $(ACTIONLINT) && $(ZIZMOR) --offline --strict-collection --collect=workflows .; else echo "Workflow audit: no workflows yet."; fi
	@cd "$(ROOT)" && $(PYTHON) scripts/check-supply-chain.py --online

release-check:
	@release_version='$(RAW_VERSION)' && \
	RELEASE_VERSION="$$release_version" $(PYTHON) -c 'import os,re,sys; value=os.environ.get("RELEASE_VERSION", ""); sys.exit(0) if re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value) else sys.exit("VERSION must be an exact stable semantic version")' && \
	$(MAKE) --no-print-directory verify PLUGIN_VERSION="$$release_version" PLUGIN_ZIP="$(ROOT)/build/distributions/perf-sentinel-$$release_version.zip" GRADLE_FLAGS="$(GRADLE_FLAGS) -Pversion=$$release_version" && \
	$(MAKE) --no-print-directory security
