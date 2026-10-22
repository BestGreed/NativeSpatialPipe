// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <filesystem>
#include <fstream>
#include <string>
#include <cstdint>
#include <stdexcept>
namespace spatial {
inline constexpr unsigned DiagnosticPostSeconds=600;
class RollingDiagnosticFile {
    std::filesystem::path directory_;std::string name_,extension_,incident_;unsigned slots_,slot_{};
    std::uint64_t limit_,bytes_{};std::ofstream file_;
public:
    RollingDiagnosticFile(const std::filesystem::path& d,std::string n,unsigned slots,std::uint64_t limit,bool json=false)
        :directory_(d),name_(std::move(n)),extension_(json?".jsonl":".sad"),slots_(slots),limit_(limit){}
    auto Path(unsigned slot)const{return directory_/(incident_+name_+"-"+std::to_string(slot)+extension_);}
    void Close(){if(file_.is_open()){file_.flush();if(!file_)throw std::runtime_error("Diagnostic flush failed");file_.close();}}
    void Write(const char* data,std::size_t size){
        if(file_.is_open()&&bytes_+size>limit_){Close();slot_=incident_.empty()?(slot_+1)%slots_:slot_+1;bytes_=0;}
        if(!file_.is_open()){file_.open(Path(slot_),std::ios::binary|std::ios::trunc);bytes_=0;}
        file_.write(data,static_cast<std::streamsize>(size));bytes_+=size;
        if(!file_)throw std::runtime_error("Diagnostic disk write failed");
    }
    void Flush(){if(file_.is_open()){file_.flush();if(!file_)throw std::runtime_error("Diagnostic flush failed");}}
    void BeginIncident(const std::string& name){
        if(!incident_.empty())throw std::runtime_error("Incident already active");
        Close();for(unsigned i=0;i<slots_;++i)if(std::filesystem::exists(Path(i)))
            std::filesystem::rename(Path(i),directory_/(name+"-before-"+Path(i).filename().string()));
        incident_=name+"-after-";slot_=0;bytes_=0;
    }
    void EndIncident(){Close();incident_.clear();slot_=0;bytes_=0;}
};
}
