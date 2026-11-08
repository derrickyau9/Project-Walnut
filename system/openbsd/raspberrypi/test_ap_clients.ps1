$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[void][Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime]
[void][Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
$taskAdapter=Get-NetAdapter -Name 'Ethernet'
if($taskAdapter.Status -ne 'Up'){throw 'Expected project Ethernet uplink is not Up.'}
$taskProfiles=@([Windows.Networking.Connectivity.NetworkInformation]::GetConnectionProfiles() | Where-Object {$_.NetworkAdapter.NetworkAdapterId -eq [guid]$taskAdapter.InterfaceGuid})
if($taskProfiles.Count -ne 1){throw 'Expected unique project Ethernet profile.'}
$taskManager=[Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($taskProfiles[0])
$taskState=$taskManager.TetheringOperationalState.ToString()
$taskClients=@()
if($taskState -eq 'On'){
    if($taskManager.GetCurrentAccessPointConfiguration().Ssid -ne 'DaOS-WiFi-Driver-Test'){
        throw 'Active hotspot is not the owned DaOS test session.'
    }
    foreach($taskClient in $taskManager.GetTetheringClients()){
        $taskMac=($taskClient.MacAddress -replace '[:-]','').ToUpperInvariant()
        if($taskMac -notmatch '^[0-9A-F]{12}$'){throw 'Unexpected client hardware address.'}
        $taskNeighbors=@(Get-NetNeighbor -AddressFamily IPv4 | Where-Object {
            ($_.LinkLayerAddress -replace '[:-]','').ToUpperInvariant() -eq $taskMac
        } | ForEach-Object {
            $taskLocal=@(Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $_.InterfaceIndex | Select-Object -ExpandProperty IPAddress)
            [pscustomobject]@{ip=$_.IPAddress;interface_index=$_.InterfaceIndex;state=$_.State.ToString();host_ipv4=$taskLocal}
        })
        $taskClients += [pscustomobject]@{
            mac=$taskClient.MacAddress
            host_names=@($taskClient.HostNames | ForEach-Object {$_.CanonicalName})
            ipv4_neighbors=$taskNeighbors
        }
    }
}
[pscustomobject]@{
    format='DaOS-owned-test-AP-clients-readonly-v1'
    observed_utc=[DateTime]::UtcNow.ToString('o')
    tethering_state=$taskState
    clients=$taskClients
    read_only=$true
    credential_file_read=$false
} | ConvertTo-Json -Depth 6
