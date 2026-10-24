# Development application access and identity

The desktop product version is 0.3.0-dev; the shared core Python package has its own version. An accepted release remains pending native acceptance. Builds must retain source commit, dirty-tree status and native-shell changes separately from the product version.

A designated development app can set an absolute existing `data_home` in its private `Contents/Resources/desktop-config.json`. The shell forwards that profile to its owned worker even when Finder or Dock launches it without environment variables. It does not copy or reset an installation. Keep this host-specific configuration outside Git. Configuration is local administrator-controlled, not an authenticated remote command channel.

Development installations continue to use signed, installation-bound offline pilot entitlements. The existing administrator CLI limits each issued entitlement to 30 days; persistent relaunch access does not imply perpetual licensing. Keep the private issuer outside source and exports, transfer only each recipient's code to that machine, and retain expiry/revocation checks. A development entitlement neither authorizes model dispatch nor lifts pause, purpose, approval, transport or evidence gates.

Before selecting an app, inventory running executable paths, bundle IDs, versions, profile locations and source metadata. Create consistent SQLite backups and hash manifests before activation or launch changes. Retire only identified processes after backup; retain old apps and profiles as recovery evidence. Use a separate development bundle identity to avoid confusing old pilot windows with the normal launch. Check retained work, quit/relaunch, Finder, Dock and Command-Tab separately; a successful HTTP check alone is not native acceptance.
