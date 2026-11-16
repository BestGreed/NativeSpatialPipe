// Local identity metadata server. Never sends or processes PCM.
#include <winsock2.h>
#include <ws2tcpip.h>
#include <fstream>
#include "binding_core.h"
static uint64_t started(HANDLE h){FILETIME c,e,k,u;if(!GetProcessTimes(h,&c,&e,&k,&u))return 0;ULARGE_INTEGER v;v.LowPart=c.dwLowDateTime;v.HighPart=c.dwHighDateTime;return v.QuadPart;}
static bool edge_matches(DWORD pid,uintptr_t expectedBase,EdgeVtables& vtables){
 HANDLE snap=CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,pid);MODULEENTRY32W m{sizeof(m)};std::wstring path;
 if(snap!=INVALID_HANDLE_VALUE){if(Module32FirstW(snap,&m))do{if(!_wcsicmp(m.szModule,L"msedge.dll")&&(uintptr_t)m.modBaseAddr==expectedBase){path=m.szExePath;break;}}while(Module32NextW(snap,&m));CloseHandle(snap);}if(path.empty())return false;
 HANDLE file=CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,nullptr,OPEN_EXISTING,0,nullptr);if(file==INVALID_HANDLE_VALUE)return false;
 BCRYPT_ALG_HANDLE alg=nullptr;BCRYPT_HASH_HANDLE hash=nullptr;BYTE digest[32]{},data[65536];DWORD n;bool ok=BCryptOpenAlgorithmProvider(&alg,BCRYPT_SHA256_ALGORITHM,nullptr,0)>=0&&BCryptCreateHash(alg,&hash,nullptr,0,nullptr,0,0)>=0;
 while(ok&&ReadFile(file,data,sizeof(data),&n,nullptr)&&n)ok=BCryptHashData(hash,data,n,0)>=0;if(ok)ok=BCryptFinishHash(hash,digest,32,0)>=0;
 char hex[65]{};for(unsigned i=0;i<32;i++)sprintf(hex+i*2,"%02x",digest[i]);
 if(hash)BCryptDestroyHash(hash);if(alg)BCryptCloseAlgorithmProvider(alg,0);CloseHandle(file);
 if(!ok)return false;
 if(!strcmp(hex,"033d957e39d32aeca0f4b457b325ea8829306c3d9324da05c7cf5378acff51ae")){vtables=edge156Vtables;return true;}
 // Edge 157.0.4341.42. The object member offsets are shared with 156,
 // while all five vtables moved. Keep both profiles exact-image checked.
 if(!strcmp(hex,"9f3773d5f287cdb958e08ef0aeaaa44280dcd150ceacf8327ff17475ba38f20b")){
  vtables={0x11a21498,0x11a21298,0x11a21318,0x11a213b8,0x11a21358};return true;
 }
 return false;
}
static std::string links(BrowserBindingCore&b){std::ostringstream o;o<<"[";for(size_t i=0;i<b.links.size();i++){auto&l=b.links[i];if(i)o<<",";o<<"{\"controller\":\""<<l.logical.text<<"\",\"waos\":\""<<l.physical.text<<"\",\"queue\":\""<<l.queue.base<<"\",\"mapping\":\""<<l.queue.mapping<<"\",\"control\":\""<<l.queue.control<<"\",\"sourcePid\":"<<b.p.source<<",\"hostPid\":"<<b.p.host<<",\"sourceStarted\":\""<<started(b.client)<<"\"}";}o<<"]";return o.str();}
static bool send_line(SOCKET s,const std::string&text){size_t at=0;while(at<text.size()){int n=send(s,text.data()+at,(int)(text.size()-at),0);if(n<=0)return false;at+=n;}return true;}
int wmain(int argc,wchar_t**argv){
 if(argc!=7)return 2;DWORD host=wcstoul(argv[1],nullptr,10),client=wcstoul(argv[2],nullptr,10),parent=wcstoul(argv[6],nullptr,10);uintptr_t base=wcstoull(argv[3],nullptr,10);
 CoInitializeEx(nullptr,COINIT_MULTITHREADED);BrowserBindingCore b;
 if(!edge_matches(client,base,b.vtables))return 6;
 if(!b.init(host,client,base,argv[5]))return 3;
 HANDLE parentHandle=OpenProcess(SYNCHRONIZE,FALSE,parent);if(!parentHandle)return 7;WSADATA w;WSAStartup(MAKEWORD(2,2),&w);SOCKET server=socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);sockaddr_in a{};a.sin_family=AF_INET;a.sin_addr.s_addr=htonl(INADDR_LOOPBACK);a.sin_port=0;
 if(bind(server,(sockaddr*)&a,sizeof(a))||listen(server,1))return 4;int len=sizeof(a);getsockname(server,(sockaddr*)&a,&len);
 {std::ofstream out(argv[4]);out<<"{\"port\":"<<ntohs(a.sin_port)<<",\"pid\":"<<GetCurrentProcessId()<<"}";}
 SOCKET peer=INVALID_SOCKET;double limit=nowms()+15000;while(nowms()<limit&&WaitForSingleObject(parentHandle,0)!=WAIT_OBJECT_0){fd_set r;FD_ZERO(&r);FD_SET(server,&r);timeval t{0,100000};if(select(0,&r,nullptr,nullptr,&t)>0){peer=accept(server,nullptr,nullptr);break;}}
 if(peer==INVALID_SOCKET)return 5;std::string buffer;bool running=true;double next=nowms()+100;
 while(running&&WaitForSingleObject(parentHandle,0)!=WAIT_OBJECT_0){DWORD code=STILL_ACTIVE;if((GetExitCodeProcess(b.client,&code)&&code!=STILL_ACTIVE)||(GetExitCodeProcess(b.p.process,&code)&&code!=STILL_ACTIVE))break;fd_set r;FD_ZERO(&r);FD_SET(peer,&r);double wait=(std::max)(0.0,next-nowms());timeval tv{(long)(wait/1000),(long)((uint64_t)(wait*1000)%1000000)};
  int selected=select(0,&r,nullptr,nullptr,&tv);if(selected<0)break;if(selected>0){char chunk[8192];int n=recv(peer,chunk,sizeof(chunk),0);if(n<=0)break;buffer.append(chunk,n);if(buffer.size()>65536)break;size_t end;
   while((end=buffer.find('\n'))!=std::string::npos){auto line=buffer.substr(0,end);buffer.erase(0,end+1);std::istringstream in(line);std::string command;unsigned seq=0;in>>command>>seq;std::ostringstream out;out<<"{\"seq\":"<<seq;
    if(command=="bind"){size_t physical=0,logical=0;in>>physical>>logical;bool valid=physical>0&&physical<=128&&logical>0&&logical<=128;std::vector<Token>tokens(valid?physical+logical:0);
     for(auto&t:tokens){in>>t.text;if(t.text.size()!=32||!std::all_of(t.text.begin(),t.text.end(),[](unsigned char c){return !!isxdigit(c);}))valid=false;else{t.a=strtoull(t.text.substr(0,16).c_str(),nullptr,16);t.b=strtoull(t.text.substr(16).c_str(),nullptr,16);}}
     double start=nowms(),structural=0,live=0;std::string reason;bool ok=valid&&b.bind(tokens,physical,reason,structural,live);if(!ok)b.clear();out<<",\"ok\":"<<(ok?"true":"false")<<",\"wallMs\":"<<nowms()-start<<",\"reason\":\""<<(valid?reason:"invalid request")<<"\",\"links\":"<<links(b);
    }else if(command=="clear"){b.clear();out<<",\"ok\":true,\"links\":[]";}else if(command=="quit"){running=false;out<<",\"ok\":true";}else out<<",\"ok\":false";
    out<<",\"validatedQpcNs\":\""<<pcm_qpc_ns()<<"\"}\n";if(!send_line(peer,out.str()))running=false;
   }
  }if(nowms()>=next){if(!b.links.empty()){bool valid=b.validate();std::string event=std::string("{\"event\":\"")+(valid?"validated":"invalidated")+"\",\"validatedQpcNs\":\""+std::to_string(pcm_qpc_ns())+"\",\"links\":"+links(b)+"}\n";if(!send_line(peer,event))break;}next=nowms()+100;}
 }
 b.clear();closesocket(peer);closesocket(server);CloseHandle(parentHandle);WSACleanup();CoUninitialize();return 0;
}
