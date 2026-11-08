// Included inside CameraDaemon::Impl; shares the authority's mutex/transaction.
void library_init() {
    exec("CREATE TABLE IF NOT EXISTS library_assets(photo_uuid TEXT PRIMARY KEY,raw_uuid TEXT NOT NULL,origin TEXT NOT NULL,metadata TEXT NOT NULL,relative_path TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'active',protected INTEGER NOT NULL DEFAULT 0,trashed INTEGER NOT NULL DEFAULT 0,purge_operation TEXT);"
         "CREATE TABLE IF NOT EXISTS library_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,photo_uuid TEXT NOT NULL,kind TEXT NOT NULL);"
         "CREATE INDEX IF NOT EXISTS library_change_photo ON library_changes(photo_uuid,seq);"
         "CREATE TRIGGER IF NOT EXISTS library_asset_insert AFTER INSERT ON library_assets BEGIN INSERT INTO library_changes(photo_uuid,kind) VALUES(new.photo_uuid,'upsert'); END;"
         "CREATE TRIGGER IF NOT EXISTS library_asset_update AFTER UPDATE ON library_assets BEGIN INSERT INTO library_changes(photo_uuid,kind) VALUES(new.photo_uuid,new.state); END;");
    { Statement s(database.value,"SELECT value FROM meta WHERE key='library_id'"); if(s.step()==SQLITE_ROW) library_id=s.text(0); }
    if(library_id.empty()) {
        const auto token=random_token();
        library_id=token.substr(0,8)+"-"+token.substr(8,4)+"-"+token.substr(12,4)+"-"+token.substr(16,4)+"-"+token.substr(20,12);
        transaction([&]{set_meta("library_id",library_id);});
    }
    if(!valid_uuid(library_id)) throw DaemonError("journal_error","invalid library identity");
    std::vector<Json> records;
    { Statement s(database.value,"SELECT record_json FROM captures WHERE status='complete'"); while(s.step()==SQLITE_ROW) records.push_back(Json::parse(s.text(0))); }
    transaction([&]{for(const auto& record:records) library_capture(record);});
    std::vector<std::pair<std::string,std::string>> pending;
    { Statement s(database.value,"SELECT photo_uuid,purge_operation FROM library_assets WHERE state='purge_pending'"); while(s.step()==SQLITE_ROW) pending.emplace_back(s.text(0),s.text(1)); }
    for(const auto& item:pending) {
        try { library_purge(item.first,item.second); }
        catch(const DaemonError&) { /* Preserve an unconfirmed asset intent; never replay capture. */ }
    }
}

void library_capture(const Json& record) {
    const auto id=record.at("request_id").get<std::string>();
    { Statement s(database.value,"SELECT 1 FROM library_assets WHERE photo_uuid=?");s.bind(1,id);if(s.step()==SQLITE_ROW)return; }
    const bool full=record.at("sensor_mode")=="sim-full";
    Json meta={{"photo_uuid",id},{"original_request_uuid",id},{"origin_kind","capture"},{"media_type","image/x-adobe-dng"},{"extension",".dng"},
      {"request_id",id},{"capture_id",id},{"frame_id",id},{"status","complete"},{"width",full?6064:128},{"height",full?4040:96},
      {"bit_depth",14},{"cfa","RGGB"},{"simulated",true},{"filename",id+".dng"}};
    for(const auto* key:{"timestamp","sensor_mode","sensor_id","calibration_id","requested_exposure_us","actual_exposure_us","requested_analogue_gain","actual_analogue_gain"})meta[key]=record.at(key);
    std::error_code ec;const auto size=std::filesystem::file_size(photos/(id+".dng"),ec);meta["byte_count"]=ec?0:size;
    Statement s(database.value,"INSERT INTO library_assets(photo_uuid,raw_uuid,origin,metadata,relative_path) VALUES(?,?,?,?,?)");
    s.bind(1,id);s.bind(2,id);s.bind(3,"capture");s.bind(4,meta.dump());s.bind(5,"photos/"+id+".dng");s.step();
}

Json library_asset(const std::string& id) {
    if(!valid_uuid(id))throw DaemonError("invalid_request","canonical media UUID required");
    Statement s(database.value,"SELECT metadata,state,protected,trashed FROM library_assets WHERE photo_uuid=?");s.bind(1,id);
    if(s.step()!=SQLITE_ROW)throw DaemonError("not_found","library photo not found");
    auto result=Json::parse(s.text(0));result["library_id"]=library_id;result["state"]=s.text(1);result["protected"]=s.number(2)!=0;result["trashed"]=s.number(3)!=0;
    Statement change(database.value,"SELECT coalesce(max(seq),0) FROM library_changes WHERE photo_uuid=?");change.bind(1,id);change.step();result["change_seq"]=change.number(0);
    return result;
}

std::filesystem::path library_path(const Json& asset) {
    const auto id=asset.at("photo_uuid").get<std::string>();const auto extension=asset.value("extension",std::string(".dng"));
    if(!valid_uuid(id)||!std::set<std::string>{".dng",".jpg",".png",".tif"}.count(extension))throw DaemonError("invalid_request","invalid asset path");
    const auto directory=root/(asset.at("origin_kind")=="capture"?"photos":"library-imports");
    std::error_code ec;
    if(std::filesystem::is_symlink(directory,ec)||std::filesystem::canonical(directory,ec)!=directory||ec)throw DaemonError("storage_error","media directory is redirected");
    const auto path=directory/(id+extension);
    if(std::filesystem::is_symlink(path,ec)||!std::filesystem::is_regular_file(path,ec)||ec)throw DaemonError("capture_unavailable","media is not a regular file");
    return path;
}

Json library_purge(const std::string& id,const std::string& operation) {
    auto asset=library_asset(id);
    if(asset.at("state")=="purged"){asset["purge_result"]="already_purged";return asset;}
    if(asset.at("protected")==true||asset.at("trashed")!=true){asset["purge_result"]="skipped_protected_or_not_trashed";return asset;}
    bool pinned=active==id;for(const auto& pin:library_pins)pinned=pinned||pin.second==id;
    if(pinned){asset["purge_result"]="skipped_in_use";return asset;}
    transaction([&]{Statement s(database.value,"UPDATE library_assets SET state='purge_pending',purge_operation=? WHERE photo_uuid=?");s.bind(1,operation);s.bind(2,id);s.step();});
    std::error_code ec;
    const auto directory=root/(asset.at("origin_kind")=="capture"?"photos":"library-imports");
    const auto expected=directory/(id+asset.value("extension",std::string(".dng")));
    const bool exists=std::filesystem::exists(expected,ec);
    if(ec)throw DaemonError("storage_error","asset existence cannot be confirmed; purge intent preserved");
    if(exists) {
        const auto path=library_path(asset);
        if(!std::filesystem::remove(path,ec)||ec) {
            transaction([&]{Statement s(database.value,"UPDATE library_assets SET state='active' WHERE photo_uuid=?");s.bind(1,id);s.step();});
            asset=library_asset(id);asset["purge_result"]="skipped_in_use_or_io";return asset;
        }
#ifndef _WIN32
        const int fd=::open(directory.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW);
        if(fd<0)throw DaemonError("storage_error","purged directory sync failed");
        const int synced=::fsync(fd);::close(fd);if(synced)throw DaemonError("storage_error","purge completion is unconfirmed");
#endif
    }
    transaction([&]{Statement s(database.value,"UPDATE library_assets SET state='purged' WHERE photo_uuid=?");s.bind(1,id);s.step();});
    asset=library_asset(id);asset["purge_result"]="purged";return asset;
}

Json library_operation(const std::string& operation,const Json& p) {
    if(!p.is_object())throw DaemonError("invalid_request","library parameters must be an object");
    if(operation=="info") {
        Statement s(database.value,"SELECT coalesce(max(seq),0) FROM library_changes");s.step();
        Statement a(database.value,"SELECT coalesce(max(rowid),0) FROM library_assets");a.step();
        return {{"library_id",library_id},{"schema_version",1},{"change_seq",s.number(0)},{"asset_high_water",a.number(0)}};
    }
    if(operation=="list"||operation=="changes") {
        const auto limit=p.value("limit",Json(50)),after=p.value("after",Json(0));
        if(!integer(limit)||limit<1||limit>50||!integer(after)||after<0)throw DaemonError("invalid_request","bounded library page required");
        std::string sql=operation=="list"?"SELECT rowid,photo_uuid FROM library_assets WHERE rowid>?":"SELECT seq,photo_uuid,kind FROM library_changes WHERE seq>?";
        const bool upper=operation=="list"&&p.contains("upper")&&!p.at("upper").is_null();
        if(upper)sql+=" AND rowid<=?";
        sql+=operation=="list"?" ORDER BY rowid LIMIT ?":" ORDER BY seq LIMIT ?";
        Statement s(database.value,sql.c_str());s.bind_integer(1,after.get<std::int64_t>());int n=2;
        if(upper){if(!integer(p.at("upper")))throw DaemonError("invalid_request","integer snapshot upper required");s.bind_integer(n++,p.at("upper").get<std::int64_t>());}
        s.bind_integer(n,limit.get<int>()+1);Json items=Json::array();std::int64_t next=after.get<std::int64_t>();bool more=false;
        while(s.step()==SQLITE_ROW) {
            if(items.size()==limit.get<std::size_t>()){more=true;break;}
            next=s.number(0);const auto id=s.text(1);auto asset=library_asset(id);
            if(operation=="list")items.push_back(asset);else items.push_back({{"seq",next},{"kind",s.text(2)},{"asset",asset}});
        }
        return {{"library_id",library_id},{"items",items},{"next_after",next},{"has_more",more}};
    }
    if(operation=="register_import") {
        if(!p.contains("descriptor")||!p.at("descriptor").is_object()||p.at("descriptor").dump().size()>32768)throw DaemonError("invalid_request","bounded import descriptor required");
        auto d=p.at("descriptor");const auto id=d.value("photo_uuid",std::string()),raw=d.value("original_request_uuid",std::string());
        if(!valid_uuid(id)||!valid_uuid(raw)||d.value("origin_kind",std::string())!="import"||!d.contains("byte_count")||!integer(d.at("byte_count"))||d.at("byte_count")<8||d.at("byte_count")>536870912)throw DaemonError("invalid_request","invalid imported asset");
        const auto path=library_path(d);std::error_code ec;
        if(std::filesystem::file_size(path,ec)!=d.at("byte_count").get<std::uint64_t>()||ec)throw DaemonError("capture_unavailable","import file differs from descriptor");
        {Statement s(database.value,"SELECT metadata FROM library_assets WHERE photo_uuid=?");s.bind(1,id);if(s.step()==SQLITE_ROW){if(Json::parse(s.text(0)).value("sha256",std::string())!=d.value("sha256",std::string()))throw DaemonError("request_conflict","import identity conflict");return library_asset(id);}}
        transaction([&]{Statement s(database.value,"INSERT INTO library_assets(photo_uuid,raw_uuid,origin,metadata,relative_path) VALUES(?,?,?,?,?)");s.bind(1,id);s.bind(2,raw);s.bind(3,"import");s.bind(4,d.dump());s.bind(5,"library-imports/"+id+d.at("extension").get<std::string>());s.step();});return library_asset(id);
    }
    if(operation=="unpin"){library_pins.erase(p.value("pin",std::string()));return {{"released",true}};}
    const auto id=p.value("photo_uuid",std::string());auto asset=library_asset(id);
    if(operation=="get")return asset;
    if(operation=="flags") {
        if(!p.contains("protected")||!p.at("protected").is_boolean()||!p.contains("trashed")||!p.at("trashed").is_boolean())throw DaemonError("invalid_request","boolean flags required");
        if(asset.at("state")!="active")throw DaemonError("capture_unavailable","asset is not active");
        if(p.contains("expected_change_seq")&&!p.at("expected_change_seq").is_null()&&p.at("expected_change_seq")!=asset.at("change_seq"))throw DaemonError("request_conflict","asset guard revision changed");
        if(p.at("protected")==true&&p.at("trashed")==true)throw DaemonError("protected","protected photo cannot enter trash");
        transaction([&]{Statement s(database.value,"UPDATE library_assets SET protected=?,trashed=? WHERE photo_uuid=?");s.bind_integer(1,p.at("protected").get<bool>()?1:0);s.bind_integer(2,p.at("trashed").get<bool>()?1:0);s.bind(3,id);s.step();});return library_asset(id);
    }
    if(operation=="pin") {
        if(asset.at("state")!="active")throw DaemonError("capture_unavailable","asset is unavailable");
        if(library_pins.size()>=64)throw DaemonError("camera_busy","asset reader limit reached");
        const auto token=random_token();library_pins[token]=id;return {{"pin",token},{"photo_uuid",id}};
    }
    if(operation=="purge") {
        if(p.value("confirmation",std::string())!="PERMANENTLY_DELETE"||!valid_uuid(p.value("operation_id",std::string())))throw DaemonError("confirmation_required","explicit purge confirmation required");
        return library_purge(id,p.at("operation_id").get<std::string>());
    }
    throw DaemonError("method_not_found","unsupported library method");
}
