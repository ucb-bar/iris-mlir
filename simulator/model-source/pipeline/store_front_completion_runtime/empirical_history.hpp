#pragma once
#include <deque>
#include <stdexcept>
#include <cstdint>
namespace iris {
// Absolute IDs remain monotonic while storage can discard an inactive prefix.
// Completed dependencies older than the prefix are already satisfied at now;
// they need neither callback nor historical payload to schedule future work.
template<class T> class IndexedHistory {
 std::deque<T> data_;uint64_t base_=0;
 T completed_;
 public:
 explicit IndexedHistory(T completed=T{}):completed_(std::move(completed)){}
 uint64_t size()const{return base_+data_.size();}
 uint64_t base()const{return base_;}
 size_t retained()const{return data_.size();}
 bool empty()const{return data_.empty();}
 void push_back(T value){data_.push_back(std::move(value));}
 T&back(){return data_.back();}const T&back()const{return data_.back();}
 T&front(){return data_.front();}const T&front()const{return data_.front();}
 void pop_front(){if(data_.empty())throw std::logic_error("empty history prefix");data_.pop_front();++base_;}
 T&at(uint64_t id){if(id<base_)return completed_;if(id>=size())throw std::out_of_range("history ID");return data_.at(id-base_);}
 const T&at(uint64_t id)const{if(id<base_)return completed_;if(id>=size())throw std::out_of_range("history ID");return data_.at(id-base_);}
 T&operator[](uint64_t id){return at(id);}const T&operator[](uint64_t id)const{return at(id);}
 auto begin(){return data_.begin();}auto end(){return data_.end();}
 auto begin()const{return data_.begin();}auto end()const{return data_.end();}
};
}
