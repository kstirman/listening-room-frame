/* Export music names only from a stable COPY of JPLAY's Realm database.
 * Usage: node export_jplay.cjs /path/default.realm /private/export.json
 * Optional dependency: realm@20.2.0. Never opens the original in Realm.
 */
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const crypto=require('node:crypto');
const Realm=require('realm').Realm;
const [input,output]=process.argv.slice(2);
if(!input||!output||path.resolve(input)===path.resolve(output))throw Error('Provide distinct input and export paths');
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'frame-jplay-'));
let realm;
try {
  const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
  const before=fs.readFileSync(input);
  const snapshot=path.join(tmp,'snapshot.realm');
  fs.writeFileSync(snapshot,before,{mode:0o600});
  if(hash(before)!==hash(fs.readFileSync(input)))throw Error('JPLAY changed during snapshot; retry later');
  // Newer Realm readers may upgrade the disposable copy, never the original.
  realm=new Realm({path:snapshot});
  const albums={};
  for(const a of realm.objects('QobuzAlbumObject'))albums[String(a.id)]={
    album_id:String(a.id),album:a.title,artist:a.artistName,version:a.version,
    expected_tracks:a.numberOfTracks,volumes:a.numberOfVolumes,
    duration_seconds:a.durationInSeconds,releaseDate:a.releaseDate,genre:a.genreName,tracks:{}};
  for(const t of realm.objects('QobuzTrackObject')){
    const aid=String(t.albumId);
    if(!albums[aid])albums[aid]={album_id:aid,album:t.albumTitle,artist:t.artistName,tracks:{}};
    albums[aid].tracks[String(t.id)]={title:t.title,version:t.version,album:t.albumTitle,
      artist:t.artistName,composer:t.composerName,work:t.work,track_number:t.trackNumber,
      duration_seconds:t.durationInSeconds,releaseDate:t.albumReleaseDate};
  }
  const document={schema:1,source:'JPLAY local music database',albums:Object.values(albums)};
  const dest=path.resolve(output), staged=dest+'.'+process.pid+'.tmp';
  fs.writeFileSync(staged,JSON.stringify(document),{mode:0o600,flag:'wx'});
  fs.renameSync(staged,dest);
  console.log(JSON.stringify({albums:document.albums.length,tracks:realm.objects('QobuzTrackObject').length}));
} finally {
  if(realm)realm.close();
  fs.rmSync(tmp,{recursive:true,force:true});
}
process.exit(0);
