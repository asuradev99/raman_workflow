program toband
implicit none

integer :: nkpt,nband,nspin,nlines,option
integer :: natom,nelectron,d1,d2,m,n
integer :: i,j,k,reason
integer :: index_buffer
real*8 :: energy_buffer, kline, tmp
real*8 ::  pi=DACOS(-1.0D0)
real*8 :: lattice(3,3)
real*8 :: lattice_reciprocal(3,3)
real*8 ::  k_VBM(3), k_CBM(3)
real*8,allocatable :: k_x(:),k_y(:),k_z(:),kpath_len(:),x_value(:)
real*8,allocatable :: k_x_frac(:),k_y_frac(:),k_z_frac(:)
real*8,allocatable :: eigens_up(:,:),eigens_dw(:,:)
real*8,allocatable :: occupation_up(:,:),occupation_dw(:,:)
real*8,allocatable :: eigens_tmp(:)
real*8 :: E_f,bandgap,E_c,E_v
character*80 :: filename,line
character*60 ::  char1, char2, char3
character*60, allocatable :: xlabel(:)
logical :: existence, odd_electron_number
real*8 :: length


open(unit=9,file="EIGENVAL")
read(9,*) natom,d1,d2,nspin

do i=1,4
  read(9,*) 
end do

read(9,*) nelectron,nkpt,nband

allocate(k_x_frac(nkpt),k_y_frac(nkpt),k_z_frac(nkpt))
allocate(occupation_up(nband,nkpt),occupation_dw(nband,nkpt))
allocate(eigens_tmp(2*nband))

if(nkpt==1) then
  allocate(eigens_up(nband,nkpt+1),eigens_dw(nband,nkpt+1))
else
  allocate(eigens_up(nband,nkpt),eigens_dw(nband,nkpt))
end if


do j=1,nkpt
  read(9,*) 
  read(9,*) k_x_frac(j),k_y_frac(j),k_z_frac(j) 
  do i=1,nband
    if(nspin==1) then
      read(9,*) line,eigens_up(i,j)
    else
      read(9,*) line,eigens_up(i,j),eigens_dw(i,j),occupation_up(i,j),occupation_dw(i,j)
    end if
  end do
end do

write(*,'(A,F14.8)') "The maximum energy transition is ", maxval(eigens_up)-minval(eigens_up)

open(unit=11,file="bandgap")

if(mod(nelectron,2)==0) then   
if(nspin==1) then           !!! It can be collinear non-spin polarized or non-collinear spin polarized    
  inquire(file="option",exist=existence)
  if(existence) then
    open(unit=1,file="option")
    read(1,*) option
    close(1)
  else
    option=1               !!! First assume it is collinear non-spin polarized 
    open(unit=12,file="INCAR")
100 do 
      read(12,"(A)",iostat=reason) line
      if(reason==0) then
        line=trim(line)
        line=adjustl(line)
        read(line,*,end=100) char1
        if(char1=="LNONCOLLINEAR" .or. char1=="LNONCOLLINEAR=" .or. char1=="LNONCOLLINEAR=.TRUE.") then
          option=0
          exit
        end if
      else
        exit
      end if
    end do
  end if
  if(option==1) nelectron=nelectron/2
  E_c=100000000
  E_v=-100000000
  do j=1,nkpt
    do i=1,nband
      eigens_tmp(i)=eigens_up(i,j)
    end do
  do i=1,(nband-1)
    energy_buffer=eigens_tmp(i)
    index_buffer=i
    do k=i+1,nband
      if(eigens_tmp(k) .lt. energy_buffer) then
        energy_buffer=eigens_tmp(k)
        index_buffer=k
      end if
    end do
    if(index_buffer .ne. i) then
      eigens_tmp(index_buffer)=eigens_tmp(i)
      eigens_tmp(i)=energy_buffer
    end if
  end do
  if(eigens_tmp(nelectron) .gt. E_v) E_v=eigens_tmp(nelectron)
  if(eigens_tmp(nelectron+1) .lt. E_c) E_c=eigens_tmp(nelectron+1)
  end do

  E_f=E_v
  bandgap=E_c-E_v
  
  do i=1,nband
    do j=1,nkpt 
      if(eigens_up(i,j)==E_v) then
        k_VBM(1)=k_x_frac(j); k_VBM(2)=k_y_frac(j); k_VBM(3)=k_z_frac(j) 
      end if
      if(eigens_up(i,j)==E_c) then
        k_CBM(1)=k_x_frac(j); k_CBM(2)=k_y_frac(j); k_CBM(3)=k_z_frac(j) 
      end if
   end do
  end do
  
  write(*,'(A,F14.8,A,3F14.8)') "The maximum valence    band is ",E_v, "   at ", k_VBM(:)
  write(*,'(A,F14.8,A,3F14.8)') "The minimum conduction band is ",E_c, "   at ", k_CBM(:)
  write(*,'(A,F14.8)') "The Fermi level is ", E_f
  write(*,'(A,F14.8)') "The bandgap is ", bandgap
  write(11,"(4F7.2)") E_f, bandgap, E_v, E_c
  write(11,'(A,F14.8,A,3F14.8)') "The maximum valence    band is ",E_v, "   at ", k_VBM(:)
  write(11,'(A,F14.8,A,3F14.8)') "The minimum conduction band is ",E_c, "   at ", k_CBM(:)
  write(11,'(A,F14.8)') "The Fermi level is ", E_f
  write(11,'(A,F14.8)') "The bandgap is ", bandgap  
!   write(11,"(4F7.2)") E_f, bandgap, E_v, E_c
  
  do i=1,nband
    do j=1,nkpt
       eigens_up(i,j)=eigens_up(i,j)-E_f
    end do
  end do
  
  open(unit=1,file='eigenvalue')
  do i=1,nband
   do j=1,nkpt
      write(1,*) j,eigens_up(i,j)
    end do
    if(nkpt==1) then
      eigens_up(:,2)=eigens_up(:,1)
      write(1,*) 2,eigens_up(i,2)
    end if
    write(1,*) 
    write(1,*) 
  end do
  do i=1,nband
   do j=1,nkpt
      write(1,*) j,eigens_up(i,j)
    end do
    if(nkpt==1) then
      eigens_up(:,2)=eigens_up(:,1)
      write(1,*) 2,eigens_up(i,2)
    end if    
    write(1,*) 
    write(1,*) 
  end do
  close(1)
  
  open(unit=1,file='eigenvalue_nonshift')
  do i=1,nband
   do j=1,nkpt
      write(1,*) j,eigens_up(i,j)+E_f
    end do
    write(1,*) 
    write(1,*) 
  end do
  do i=1,nband
   do j=1,nkpt
      write(1,*) j,eigens_up(i,j)+E_f
    end do
    write(1,*) 
    write(1,*) 
  end do
  close(1) 
 
else                          !!! nspin==2
  E_c=100000000
  E_v=-100000000
  do j=1,nkpt
    do i=1,nband
      eigens_tmp(i)=eigens_up(i,j)
      eigens_tmp(i+nband)=eigens_dw(i,j)
    end do
  do i=1,(2*nband-1)
    energy_buffer=eigens_tmp(i)
    index_buffer=i
    do k=i+1,2*nband
      if(eigens_tmp(k) .lt. energy_buffer) then
        energy_buffer=eigens_tmp(k)
        index_buffer=k
      end if
    end do
    if(index_buffer .ne. i) then
      eigens_tmp(index_buffer)=eigens_tmp(i)
      eigens_tmp(i)=energy_buffer
    end if
  end do
  if(eigens_tmp(nelectron) .gt. E_v) E_v=eigens_tmp(nelectron)
  if(eigens_tmp(nelectron+1) .lt. E_c) E_c=eigens_tmp(nelectron+1)
  end do

  E_f=E_v
  bandgap=E_c-E_v

  do i=1,nband
    do j=1,nkpt 
      if(eigens_up(i,j)==E_v) then
        k_VBM(1)=k_x_frac(j); k_VBM(2)=k_y_frac(j); k_VBM(3)=k_z_frac(j) 
      end if
      if(eigens_up(i,j)==E_c) then
        k_CBM(1)=k_x_frac(j); k_CBM(2)=k_y_frac(j); k_CBM(3)=k_z_frac(j) 
      end if
      if(eigens_dw(i,j)==E_v) then
        k_VBM(1)=k_x_frac(j); k_VBM(2)=k_y_frac(j); k_VBM(3)=k_z_frac(j) 
      end if
      if(eigens_dw(i,j)==E_c) then
        k_CBM(1)=k_x_frac(j); k_CBM(2)=k_y_frac(j); k_CBM(3)=k_z_frac(j) 
      end if      
   end do
  end do
  
  write(*,'(A,F14.8,A,3F14.8)') "The maximum valence    band is ",E_v, "   at ", k_VBM(:)
  write(*,'(A,F14.8,A,3F14.8)') "The minimum conduction band is ",E_c, "   at ", k_CBM(:)
  write(*,'(A,F14.8)') "The Fermi level is ", E_f
  write(*,'(A,F14.8)') "The bandgap is ", bandgap
  write(11,"(4F7.2)") E_f, bandgap, E_v, E_c
  write(11,'(A,F14.8,A,3F14.8)') "The maximum valence    band is ",E_v, "   at ", k_VBM(:)
  write(11,'(A,F14.8,A,3F14.8)') "The minimum conduction band is ",E_c, "   at ", k_CBM(:)
  write(11,'(A,F14.8)') "The Fermi level is ", E_f
  write(11,'(A,F14.8)') "The bandgap is ", bandgap   
!   write(11,"(4F7.2)") E_f, bandgap, E_v, E_c
  
  do i=1,nband
    do j=1,nkpt
       eigens_up(i,j)=eigens_up(i,j)-E_f
       eigens_dw(i,j)=eigens_dw(i,j)-E_f
    end do
  end do
  
  open(unit=1,file='eigenvalue')
  do i=1,nband
   do j=1,nkpt
      write(1,*) j,eigens_up(i,j)
    end do
    write(1,*) 
    write(1,*) 
  end do
  do i=1,nband
   do j=1,nkpt
      write(1,*) j,eigens_dw(i,j)
    end do
    write(1,*) 
    write(1,*) 
  end do
  close(1)
  
  open(unit=1,file='eigenvalue_nonshift')
  do i=1,nband
   do j=1,nkpt
      write(1,*) j,eigens_up(i,j)+E_f
    end do
    write(1,*) 
    write(1,*) 
  end do
  do i=1,nband
   do j=1,nkpt
      write(1,*) j,eigens_dw(i,j)+E_f
    end do
    write(1,*) 
    write(1,*) 
  end do
  close(1)
end if

  if(nkpt==1) then
    eigens_up(:,2)=eigens_up(:,1)
    eigens_dw(:,2)=eigens_dw(:,1) 
    
      open(unit=1,file='eigenvalue')
  do i=1,nband
   do j=1,nkpt
      write(1,*) j,eigens_up(i,j)
    end do
    write(1,*) 
    write(1,*) 
  end do
  do i=1,nband
   do j=1,nkpt
      write(1,*) j,eigens_dw(i,j)
    end do
    write(1,*) 
    write(1,*) 
  end do
  close(1)
  
  open(unit=1,file='eigenvalue_nonshift')
  do i=1,nband
   do j=1,nkpt
      write(1,*) j,eigens_up(i,j)+E_f
    end do
    write(1,*) 
    write(1,*) 
  end do
  do i=1,nband
   do j=1,nkpt
      write(1,*) j,eigens_dw(i,j)+E_f
    end do
    write(1,*) 
    write(1,*) 
  end do
  close(1)
  end if

else
  write(*,*) "Caution: number of electrons is odd!!!"
  
  if(nspin==1) then           !!! It can be collinear non-spin polarized or non-collinear spin polarized    
    inquire(file="option",exist=existence)
    if(existence) then
      open(unit=1,file="option")
      read(1,*) option
      close(1)
    else
      option=1               !!! First assume it is collinear non-spin polarized 
      open(unit=12,file="INCAR")
  101 do 
        read(12,"(A)",iostat=reason) line
        if(reason==0) then
          line=trim(line)
          line=adjustl(line)
          read(line,*,end=101) char1
           if(char1=="LNONCOLLINEAR" .or. char1=="LNONCOLLINEAR=" .or. char1=="LNONCOLLINEAR=.TRUE.") then
            option=0
            exit
          end if
        else
          exit
        end if
      end do
    end if
  
    if(option==0) then    !non-collinear spin
      E_c=100000000
      E_v=-100000000
      do j=1,nkpt
        do i=1,nband
         eigens_tmp(i)=eigens_up(i,j)
        end do
      do i=1,(nband-1)
        energy_buffer=eigens_tmp(i)
        index_buffer=i
        do k=i+1,nband
          if(eigens_tmp(k) .lt. energy_buffer) then
            energy_buffer=eigens_tmp(k)
            index_buffer=k
          end if
        end do
        if(index_buffer .ne. i) then
          eigens_tmp(index_buffer)=eigens_tmp(i)
          eigens_tmp(i)=energy_buffer
        end if
      end do
      if(eigens_tmp(nelectron) .gt. E_v) E_v=eigens_tmp(nelectron)
      if(eigens_tmp(nelectron+1) .lt. E_c) E_c=eigens_tmp(nelectron+1)
      end do

      E_f=E_v
      bandgap=E_c-E_v    

      do i=1,nband
        do j=1,nkpt 
          if(eigens_up(i,j)==E_v) then
            k_VBM(1)=k_x_frac(j); k_VBM(2)=k_y_frac(j); k_VBM(3)=k_z_frac(j) 
          end if
          if(eigens_up(i,j)==E_c) then
            k_CBM(1)=k_x_frac(j); k_CBM(2)=k_y_frac(j); k_CBM(3)=k_z_frac(j) 
          end if
       end do
      end do
  
      write(*,'(A,F14.8,A,3F14.8)') "The maximum valence    band is ",E_v, "   at ", k_VBM(:)
      write(*,'(A,F14.8,A,3F14.8)') "The minimum conduction band is ",E_c, "   at ", k_CBM(:)
      write(*,'(A,F14.8)') "The Fermi level is ", E_f
      write(*,'(A,F14.8)') "The bandgap is ", bandgap
      write(11,"(4F7.2)") E_f, bandgap, E_v, E_c
      write(11,'(A,F14.8,A,3F14.8)') "The maximum valence    band is ",E_v, "   at ", k_VBM(:)
      write(11,'(A,F14.8,A,3F14.8)') "The minimum conduction band is ",E_c, "   at ", k_CBM(:)
      write(11,'(A,F14.8)') "The Fermi level is ", E_f
      write(11,'(A,F14.8)') "The bandgap is ", bandgap  
!   write(11,"(4F7.2)") E_f, bandgap, E_v, E_c
  
      do i=1,nband
        do j=1,nkpt
           eigens_up(i,j)=eigens_up(i,j)-E_f
        end do
      end do
  
      open(unit=1,file='eigenvalue')
      do i=1,nband
        do j=1,nkpt
          write(1,*) j,eigens_up(i,j)
        end do
        write(1,*) 
        write(1,*) 
      end do
      do i=1,nband
       do j=1,nkpt
          write(1,*) j,eigens_up(i,j)
        end do
        write(1,*) 
        write(1,*) 
      end do
  
      close(1) 
      
      open(unit=1,file='eigenvalue_nonshift')
      do i=1,nband
        do j=1,nkpt
          write(1,*) j,eigens_up(i,j)+E_f
        end do
        write(1,*) 
        write(1,*) 
      end do
      do i=1,nband
       do j=1,nkpt
          write(1,*) j,eigens_up(i,j)+E_f
        end do
        write(1,*) 
        write(1,*) 
      end do
  
      close(1)          
  
    end if
  end if
    


  if(nspin==2) then
    E_c=100000000
    E_v=-100000000
    do j=1,nkpt
      do i=1,nband
        eigens_tmp(i)=eigens_up(i,j)
        eigens_tmp(i+nband)=eigens_dw(i,j)
      end do
    do i=1,(2*nband-1)
      energy_buffer=eigens_tmp(i)
      index_buffer=i
      do k=i+1,2*nband
        if(eigens_tmp(k) .lt. energy_buffer) then
          energy_buffer=eigens_tmp(k)
          index_buffer=k
        end if
      end do
    if(index_buffer .ne. i) then
      eigens_tmp(index_buffer)=eigens_tmp(i)
      eigens_tmp(i)=energy_buffer
    end if
    end do
    if(eigens_tmp(nelectron) .gt. E_v) E_v=eigens_tmp(nelectron)
    if(eigens_tmp(nelectron+1) .lt. E_c) E_c=eigens_tmp(nelectron+1)
    end do

    E_f=E_v
    bandgap=E_c-E_v

    do i=1,nband
      do j=1,nkpt 
        if(eigens_up(i,j)==E_v) then
          k_VBM(1)=k_x_frac(j); k_VBM(2)=k_y_frac(j); k_VBM(3)=k_z_frac(j) 
        end if
        if(eigens_up(i,j)==E_c) then
          k_CBM(1)=k_x_frac(j); k_CBM(2)=k_y_frac(j); k_CBM(3)=k_z_frac(j) 
        end if
        if(eigens_dw(i,j)==E_v) then
          k_VBM(1)=k_x_frac(j); k_VBM(2)=k_y_frac(j); k_VBM(3)=k_z_frac(j) 
        end if
        if(eigens_dw(i,j)==E_c) then
          k_CBM(1)=k_x_frac(j); k_CBM(2)=k_y_frac(j); k_CBM(3)=k_z_frac(j) 
        end if      
     end do
    end do
  
    write(*,'(A,F14.8,A,3F14.8)') "The maximum valence    band is ",E_v, "   at ", k_VBM(:)
    write(*,'(A,F14.8,A,3F14.8)') "The minimum conduction band is ",E_c, "   at ", k_CBM(:)
    write(*,'(A,F14.8)') "The Fermi level is ", E_f
    write(*,'(A,F14.8)') "The bandgap is ", bandgap
    write(11,"(4F7.2)") E_f, bandgap, E_v, E_c
    write(11,'(A,F14.8,A,3F14.8)') "The maximum valence    band is ",E_v, "   at ", k_VBM(:)
    write(11,'(A,F14.8,A,3F14.8)') "The minimum conduction band is ",E_c, "   at ", k_CBM(:)
    write(11,'(A,F14.8)') "The Fermi level is ", E_f
    write(11,'(A,F14.8)') "The bandgap is ", bandgap   
!   write(11,"(4F7.2)") E_f, bandgap, E_v, E_c
  
    do i=1,nband
      do j=1,nkpt
         eigens_up(i,j)=eigens_up(i,j)-E_f
         eigens_dw(i,j)=eigens_dw(i,j)-E_f
      end do
    end do
  
    open(unit=1,file='eigenvalue')
    do i=1,nband
     do j=1,nkpt
        write(1,*) j,eigens_up(i,j)
      end do
      write(1,*) 
      write(1,*) 
    end do
    do i=1,nband
     do j=1,nkpt
        write(1,*) j,eigens_dw(i,j)
      end do
      write(1,*) 
      write(1,*) 
    end do
    close(1) 
    
    open(unit=1,file='eigenvalue_nonshift')
    do i=1,nband
     do j=1,nkpt
        write(1,*) j,eigens_up(i,j)+E_f
      end do
      write(1,*) 
      write(1,*) 
    end do
    do i=1,nband
     do j=1,nkpt
        write(1,*) j,eigens_dw(i,j)+E_f
      end do
      write(1,*) 
      write(1,*) 
    end do
    close(1)     
  end if
end if

close(unit=7)
close(unit=9)
close(11)
 
open(unit=1,file='eigenvalue.agr')
!
! Xmgrace format 
!    
write(1,'(a)') '# Grace project file                      '    
write(1,'(a)') '@version 50113                            '    
write(1,'(a)') '@page size 792, 612                       '    
write(1,'(a)') '@page scroll 5%                           '    
write(1,'(a)') '@page inout 5%                            '    
write(1,'(a)') '@link page off                            '    
write(1,'(a)') '@with g0'                                      
write(1,'(a)') '@    world xmin 1.00'    
if(nkpt==1) then
  write(1,'(a,f10.5)') '@    world xmax ',dfloat(nkpt+1)
else 
  write(1,'(a,f10.5)') '@    world xmax ',dfloat(nkpt)
end if
write(1,'(a,f10.5)') '@    world ymin ',-2.0
write(1,'(a,f10.5)') '@    world ymax ',3.0
write(1,'(a)') '@    view 0.150000, 0.150000, 0.500000, 0.800000'
write(1,'(a)') '@default linewidth 1.5'  
write(1,'(a)') '@    xaxis  on'
write(1,'(a)') '@    xaxis  type zero false'
write(1,'(a)') '@    xaxis  offset 0.000000 , 0.000000'
write(1,'(a)') '@    xaxis  bar on'
write(1,'(a)') '@    xaxis  bar color 1'
write(1,'(a)') '@    xaxis  bar linestyle 1'
write(1,'(a)') '@    xaxis  bar linewidth 1.0'
write(1,'(a)') '@    xaxis  label ""'
write(1,'(a)') '@    xaxis  label layout para'
write(1,'(a)') '@    xaxis  label place auto'
write(1,'(a)') '@    xaxis  label char size 1.070000'
write(1,'(a)') '@    xaxis  label font 9'
write(1,'(a)') '@    xaxis  label color 1'
write(1,'(a)') '@    xaxis  label place normal'
write(1,'(a)') '@    xaxis  tick off'
write(1,'(a,i0)') '@    xaxis  tick major ',int(nkpt/5)
write(1,'(a)') '@    xaxis  tick minor ticks 1'
write(1,'(a)') '@    xaxis  tick default 6'
write(1,'(a)') '@    xaxis  tick place rounded true'
write(1,'(a)') '@    xaxis  tick in'
write(1,'(a)') '@    xaxis  tick major size 1.000000'
write(1,'(a)') '@    xaxis  tick major color 1'
write(1,'(a)') '@    xaxis  tick major linewidth 1.0'
write(1,'(a)') '@    xaxis  tick major linestyle 3'
! write(1,'(a)') '@    xaxis  tick major grid on'
write(1,'(a)') '@    xaxis  tick minor color 1'
write(1,'(a)') '@    xaxis  tick minor linewidth 1.0'
write(1,'(a)') '@    xaxis  tick minor linestyle 1'
write(1,'(a)') '@    xaxis  tick minor grid off'
write(1,'(a)') '@    xaxis  tick minor size 0.500000'
write(1,'(a)') '@    xaxis  ticklabel off'
write(1,'(a)') '@    xaxis  ticklabel format general'
write(1,'(a)') '@    xaxis  ticklabel prec 5'
write(1,'(a)') '@    xaxis  ticklabel formula ""'
write(1,'(a)') '@    xaxis  ticklabel append ""'
write(1,'(a)') '@    xaxis  ticklabel prepend ""'
write(1,'(a)') '@    xaxis  ticklabel angle 0'
write(1,'(a)') '@    xaxis  ticklabel skip 0'
write(1,'(a)') '@    xaxis  ticklabel stagger 0'
write(1,'(a)') '@    xaxis  ticklabel place normal'
write(1,'(a)') '@    xaxis  ticklabel offset auto'
write(1,'(a)') '@    xaxis  ticklabel offset 0.000000 , 0.010000'
write(1,'(a)') '@    xaxis  ticklabel start type auto'
write(1,'(a)') '@    xaxis  ticklabel start 0.000000'
write(1,'(a)') '@    xaxis  ticklabel stop type auto'
write(1,'(a)') '@    xaxis  ticklabel stop 0.000000'
write(1,'(a)') '@    xaxis  ticklabel char size 1.230000'
write(1,'(a)') '@    xaxis  ticklabel font 9'
write(1,'(a)') '@    xaxis  ticklabel color 1'
write(1,'(a)') '@    xaxis  tick place normal'
! write(1,'(a)') '@    xaxis  tick spec type both'
! write(1,'(a,i0)') '@    xaxis  tick spec ',nlines+1   
! write(1,'(a)') '@    xaxis  tick major 0, 0'    
! do i=1,nlines   
!    write(1,'(a,i0,a,a)') '@    xaxis  ticklabel ',i-1,',', '"'//trim(adjustl(xlabel(i)))//'"'    
!    write(1,'(a,i0,a,f10.5)') '@    xaxis  tick major ',i,' , ',sum(kpath_len(1:i))    
! end do    
! write(1,'(a,i0,a)') '@    xaxis  ticklabel ',nlines  &    
!      ,',"'//trim(adjustl(xlabel(nlines+1)))//'"'    
write(1,'(a)') '@    yaxis  on'
write(1,'(a)') '@    yaxis  type zero false'
write(1,'(a)') '@    yaxis  offset 0.000000 , 0.000000'
write(1,'(a)') '@    yaxis  bar on'
write(1,'(a)') '@    yaxis  bar color 1'
write(1,'(a)') '@    yaxis  bar linestyle 1'
write(1,'(a)') '@    yaxis  bar linewidth 1.0'
write(1,'(a)') '@    yaxis  label "Energy (eV)"'
write(1,'(a)') '@    yaxis  label layout para'
write(1,'(a)') '@    yaxis  label place spec'
write(1,'(a)') '@    yaxis  label place 0.000000, 0.060000'
write(1,'(a)') '@    yaxis  label char size 1.230000'
write(1,'(a)') '@    yaxis  label font 9'
write(1,'(a)') '@    yaxis  label color 1'
write(1,'(a)') '@    yaxis  label place normal'
write(1,'(a)') '@    yaxis  tick on'
write(1,'(a)') '@    yaxis  tick major 1'
write(1,'(a)') '@    yaxis  tick minor ticks 1'
write(1,'(a)') '@    yaxis  tick default 6'
write(1,'(a)') '@    yaxis  tick place rounded true'
write(1,'(a)') '@    yaxis  tick out'
write(1,'(a)') '@    yaxis  tick major size 0.830000'
write(1,'(a)') '@    yaxis  tick major color 1'
write(1,'(a)') '@    yaxis  tick major linewidth 1.5'
write(1,'(a)') '@    yaxis  tick major linestyle 1'
write(1,'(a)') '@    yaxis  tick major grid off'
write(1,'(a)') '@    yaxis  tick minor color 1'
write(1,'(a)') '@    yaxis  tick minor linewidth 1.5'
write(1,'(a)') '@    yaxis  tick minor linestyle 1'
write(1,'(a)') '@    yaxis  tick minor grid off'
write(1,'(a)') '@    yaxis  tick minor size 0.410000'
write(1,'(a)') '@    yaxis  ticklabel on'
write(1,'(a)') '@    yaxis  ticklabel format general'
write(1,'(a)') '@    yaxis  ticklabel prec 5'
write(1,'(a)') '@    yaxis  ticklabel formula ""'
write(1,'(a)') '@    yaxis  ticklabel append ""'
write(1,'(a)') '@    yaxis  ticklabel prepend ""'
write(1,'(a)') '@    yaxis  ticklabel angle 0'
write(1,'(a)') '@    yaxis  ticklabel skip 0'
write(1,'(a)') '@    yaxis  ticklabel stagger 0'
write(1,'(a)') '@    yaxis  ticklabel place normal'
write(1,'(a)') '@    yaxis  ticklabel offset auto'
write(1,'(a)') '@    yaxis  ticklabel offset 0.000000 , 0.010000'
write(1,'(a)') '@    yaxis  ticklabel start type auto'
write(1,'(a)') '@    yaxis  ticklabel start 0.000000'
write(1,'(a)') '@    yaxis  ticklabel stop type auto'
write(1,'(a)') '@    yaxis  ticklabel stop 0.000000'
write(1,'(a)') '@    yaxis  ticklabel char size 1.110000'
write(1,'(a)') '@    yaxis  ticklabel font 9'
write(1,'(a)') '@    yaxis  ticklabel color 1'
write(1,'(a)') '@    yaxis  tick place normal'
write(1,'(a)') '@    yaxis  tick spec type none'
write(1,'(a)') '@    altxaxis  off'
write(1,'(a)') '@    altyaxis  off'
write(1,'(a)') '@    legend on'
write(1,'(a)') '@    legend loctype view'
write(1,'(a)') '@    legend 0.85, 0.8'
write(1,'(a)') '@    legend box color 1'
write(1,'(a)') '@    legend box pattern 1'
write(1,'(a)') '@    legend box linewidth 1.5'
write(1,'(a)') '@    legend box linestyle 1'
write(1,'(a)') '@    legend box fill color 0'
write(1,'(a)') '@    legend box fill pattern 1'
write(1,'(a)') '@    legend font 9'
write(1,'(a)') '@    legend char size 1.000000'
write(1,'(a)') '@    legend color 1'
write(1,'(a)') '@    legend length 4'
write(1,'(a)') '@    legend vgap 1'
write(1,'(a)') '@    legend hgap 1'
write(1,'(a)') '@    legend invert false'
write(1,'(a)') '@    frame type 0'
write(1,'(a)') '@    frame linestyle 1'
write(1,'(a)') '@    frame linewidth 1.5'
write(1,'(a)') '@    frame color 1'
write(1,'(a)') '@    frame pattern 1'
write(1,'(a)') '@    frame background color 0'
write(1,'(a)') '@    frame background pattern 0'


if(nspin==1) then
  do i=1,nband    
     write(1,'(a,i0,a)') '@    s',i-1,' line linewidth 2.0' 
     write(1,'(a,i0,a)') '@    s',i-1,' line color 1' 
  end do    
  do i=1,nband
     write(1,'(a,i0)') '@target G0.S',i-1    
     write(1,'(a)') '@type xy'    
     do j=1,nkpt
        write(1,'(2E16.8)') dfloat(j),eigens_up(i,j)  
     end do
     if(nkpt==1) then
       eigens_up(:,2)=eigens_up(:,1)
       do j=2,2
          write(1,'(2E16.8)') dfloat(j),eigens_up(i,j)  
       end do    
     end if                  
     write(1,'(a,i0)') '&'    
  end do  
else
  do i=1,nband    
     write(1,'(a,i0,a)') '@    s',i-1,' line linewidth 2.0' 
     write(1,'(a,i0,a)') '@    s',i-1,' line color 1' 
  end do   
  
  do i=1,nband
     write(1,'(a,i0)') '@target G0.S',i-1    
     write(1,'(a)') '@type xy'    
     do j=1,nkpt
        write(1,'(2E16.8)') dfloat(j),eigens_up(i,j)  
     end do 
     if(nkpt==1) then
       eigens_up(:,2)=eigens_up(:,1)
       do j=2,2
          write(1,'(2E16.8)') dfloat(j),eigens_up(i,j)  
       end do    
     end if         
     write(1,'(a,i0)') '&'    
  end do  
  
  do i=1+nband,2*nband
     write(1,'(a,i0,a)') '@    s',i-1,' line linewidth 2.0' 
     write(1,'(a,i0,a)') '@    s',i-1,' line color 2' 
  end do  
  
  
  do i=1+nband,2*nband
     write(1,'(a,i0)') '@target G0.S',i-1    
     write(1,'(a)') '@type xy'    
     do j=1,nkpt
        write(1,'(2E16.8)') dfloat(j),eigens_dw(i-nband,j)  
     end do    
     if(nkpt==1) then
       eigens_dw(:,2)=eigens_dw(:,1)
       do j=2,2
          write(1,'(2E16.8)') dfloat(j),eigens_dw(i-nband,j)
       end do    
     end if 
     write(1,'(a,i0)') '&'    
  end do  
end if


close(1)

open(unit=1,file='eigenvalue_nonshift.agr')
!
! Xmgrace format 
!    
write(1,'(a)') '# Grace project file                      '    
write(1,'(a)') '@version 50113                            '    
write(1,'(a)') '@page size 792, 612                       '    
write(1,'(a)') '@page scroll 5%                           '    
write(1,'(a)') '@page inout 5%                            '    
write(1,'(a)') '@link page off                            '    
write(1,'(a)') '@with g0'                                      
write(1,'(a)') '@    world xmin 1.00'    
if(nkpt==1) then
  write(1,'(a,f10.5)') '@    world xmax ',dfloat(nkpt+1)
else 
  write(1,'(a,f10.5)') '@    world xmax ',dfloat(nkpt)
end if
write(1,'(a,f10.5)') '@    world ymin ',-6.0
write(1,'(a,f10.5)') '@    world ymax ',2.0
write(1,'(a)') '@    view 0.150000, 0.150000, 0.500000, 0.800000'
write(1,'(a)') '@default linewidth 1.5'  
write(1,'(a)') '@    xaxis  on'
write(1,'(a)') '@    xaxis  type zero false'
write(1,'(a)') '@    xaxis  offset 0.000000 , 0.000000'
write(1,'(a)') '@    xaxis  bar on'
write(1,'(a)') '@    xaxis  bar color 1'
write(1,'(a)') '@    xaxis  bar linestyle 1'
write(1,'(a)') '@    xaxis  bar linewidth 1.0'
write(1,'(a)') '@    xaxis  label ""'
write(1,'(a)') '@    xaxis  label layout para'
write(1,'(a)') '@    xaxis  label place auto'
write(1,'(a)') '@    xaxis  label char size 1.070000'
write(1,'(a)') '@    xaxis  label font 9'
write(1,'(a)') '@    xaxis  label color 1'
write(1,'(a)') '@    xaxis  label place normal'
write(1,'(a)') '@    xaxis  tick off'
write(1,'(a,i0)') '@    xaxis  tick major ',int(nkpt/5)
write(1,'(a)') '@    xaxis  tick minor ticks 1'
write(1,'(a)') '@    xaxis  tick default 6'
write(1,'(a)') '@    xaxis  tick place rounded true'
write(1,'(a)') '@    xaxis  tick in'
write(1,'(a)') '@    xaxis  tick major size 1.000000'
write(1,'(a)') '@    xaxis  tick major color 1'
write(1,'(a)') '@    xaxis  tick major linewidth 1.0'
write(1,'(a)') '@    xaxis  tick major linestyle 3'
! write(1,'(a)') '@    xaxis  tick major grid on'
write(1,'(a)') '@    xaxis  tick minor color 1'
write(1,'(a)') '@    xaxis  tick minor linewidth 1.0'
write(1,'(a)') '@    xaxis  tick minor linestyle 1'
write(1,'(a)') '@    xaxis  tick minor grid off'
write(1,'(a)') '@    xaxis  tick minor size 0.500000'
write(1,'(a)') '@    xaxis  ticklabel off'
write(1,'(a)') '@    xaxis  ticklabel format general'
write(1,'(a)') '@    xaxis  ticklabel prec 5'
write(1,'(a)') '@    xaxis  ticklabel formula ""'
write(1,'(a)') '@    xaxis  ticklabel append ""'
write(1,'(a)') '@    xaxis  ticklabel prepend ""'
write(1,'(a)') '@    xaxis  ticklabel angle 0'
write(1,'(a)') '@    xaxis  ticklabel skip 0'
write(1,'(a)') '@    xaxis  ticklabel stagger 0'
write(1,'(a)') '@    xaxis  ticklabel place normal'
write(1,'(a)') '@    xaxis  ticklabel offset auto'
write(1,'(a)') '@    xaxis  ticklabel offset 0.000000 , 0.010000'
write(1,'(a)') '@    xaxis  ticklabel start type auto'
write(1,'(a)') '@    xaxis  ticklabel start 0.000000'
write(1,'(a)') '@    xaxis  ticklabel stop type auto'
write(1,'(a)') '@    xaxis  ticklabel stop 0.000000'
write(1,'(a)') '@    xaxis  ticklabel char size 1.230000'
write(1,'(a)') '@    xaxis  ticklabel font 9'
write(1,'(a)') '@    xaxis  ticklabel color 1'
write(1,'(a)') '@    xaxis  tick place normal'
! write(1,'(a)') '@    xaxis  tick spec type both'
! write(1,'(a,i0)') '@    xaxis  tick spec ',nlines+1   
! write(1,'(a)') '@    xaxis  tick major 0, 0'    
! do i=1,nlines   
!    write(1,'(a,i0,a,a)') '@    xaxis  ticklabel ',i-1,',', '"'//trim(adjustl(xlabel(i)))//'"'    
!    write(1,'(a,i0,a,f10.5)') '@    xaxis  tick major ',i,' , ',sum(kpath_len(1:i))    
! end do    
! write(1,'(a,i0,a)') '@    xaxis  ticklabel ',nlines  &    
!      ,',"'//trim(adjustl(xlabel(nlines+1)))//'"'    
write(1,'(a)') '@    yaxis  on'
write(1,'(a)') '@    yaxis  type zero false'
write(1,'(a)') '@    yaxis  offset 0.000000 , 0.000000'
write(1,'(a)') '@    yaxis  bar on'
write(1,'(a)') '@    yaxis  bar color 1'
write(1,'(a)') '@    yaxis  bar linestyle 1'
write(1,'(a)') '@    yaxis  bar linewidth 1.0'
write(1,'(a)') '@    yaxis  label "Energy (eV)"'
write(1,'(a)') '@    yaxis  label layout para'
write(1,'(a)') '@    yaxis  label place spec'
write(1,'(a)') '@    yaxis  label place 0.000000, 0.060000'
write(1,'(a)') '@    yaxis  label char size 1.230000'
write(1,'(a)') '@    yaxis  label font 9'
write(1,'(a)') '@    yaxis  label color 1'
write(1,'(a)') '@    yaxis  label place normal'
write(1,'(a)') '@    yaxis  tick on'
write(1,'(a)') '@    yaxis  tick major 1'
write(1,'(a)') '@    yaxis  tick minor ticks 1'
write(1,'(a)') '@    yaxis  tick default 6'
write(1,'(a)') '@    yaxis  tick place rounded true'
write(1,'(a)') '@    yaxis  tick out'
write(1,'(a)') '@    yaxis  tick major size 0.830000'
write(1,'(a)') '@    yaxis  tick major color 1'
write(1,'(a)') '@    yaxis  tick major linewidth 1.5'
write(1,'(a)') '@    yaxis  tick major linestyle 1'
write(1,'(a)') '@    yaxis  tick major grid off'
write(1,'(a)') '@    yaxis  tick minor color 1'
write(1,'(a)') '@    yaxis  tick minor linewidth 1.5'
write(1,'(a)') '@    yaxis  tick minor linestyle 1'
write(1,'(a)') '@    yaxis  tick minor grid off'
write(1,'(a)') '@    yaxis  tick minor size 0.410000'
write(1,'(a)') '@    yaxis  ticklabel on'
write(1,'(a)') '@    yaxis  ticklabel format general'
write(1,'(a)') '@    yaxis  ticklabel prec 5'
write(1,'(a)') '@    yaxis  ticklabel formula ""'
write(1,'(a)') '@    yaxis  ticklabel append ""'
write(1,'(a)') '@    yaxis  ticklabel prepend ""'
write(1,'(a)') '@    yaxis  ticklabel angle 0'
write(1,'(a)') '@    yaxis  ticklabel skip 0'
write(1,'(a)') '@    yaxis  ticklabel stagger 0'
write(1,'(a)') '@    yaxis  ticklabel place normal'
write(1,'(a)') '@    yaxis  ticklabel offset auto'
write(1,'(a)') '@    yaxis  ticklabel offset 0.000000 , 0.010000'
write(1,'(a)') '@    yaxis  ticklabel start type auto'
write(1,'(a)') '@    yaxis  ticklabel start 0.000000'
write(1,'(a)') '@    yaxis  ticklabel stop type auto'
write(1,'(a)') '@    yaxis  ticklabel stop 0.000000'
write(1,'(a)') '@    yaxis  ticklabel char size 1.110000'
write(1,'(a)') '@    yaxis  ticklabel font 9'
write(1,'(a)') '@    yaxis  ticklabel color 1'
write(1,'(a)') '@    yaxis  tick place normal'
write(1,'(a)') '@    yaxis  tick spec type none'
write(1,'(a)') '@    altxaxis  off'
write(1,'(a)') '@    altyaxis  off'
write(1,'(a)') '@    legend on'
write(1,'(a)') '@    legend loctype view'
write(1,'(a)') '@    legend 0.85, 0.8'
write(1,'(a)') '@    legend box color 1'
write(1,'(a)') '@    legend box pattern 1'
write(1,'(a)') '@    legend box linewidth 1.5'
write(1,'(a)') '@    legend box linestyle 1'
write(1,'(a)') '@    legend box fill color 0'
write(1,'(a)') '@    legend box fill pattern 1'
write(1,'(a)') '@    legend font 9'
write(1,'(a)') '@    legend char size 1.000000'
write(1,'(a)') '@    legend color 1'
write(1,'(a)') '@    legend length 4'
write(1,'(a)') '@    legend vgap 1'
write(1,'(a)') '@    legend hgap 1'
write(1,'(a)') '@    legend invert false'
write(1,'(a)') '@    frame type 0'
write(1,'(a)') '@    frame linestyle 1'
write(1,'(a)') '@    frame linewidth 1.5'
write(1,'(a)') '@    frame color 1'
write(1,'(a)') '@    frame pattern 1'
write(1,'(a)') '@    frame background color 0'
write(1,'(a)') '@    frame background pattern 0'


if(nspin==1) then
  do i=1,nband    
     write(1,'(a,i0,a)') '@    s',i-1,' line linewidth 2.0' 
     write(1,'(a,i0,a)') '@    s',i-1,' line color 1' 
  end do    
  do i=1,nband
     write(1,'(a,i0)') '@target G0.S',i-1    
     write(1,'(a)') '@type xy'    
     do j=1,nkpt
        write(1,'(2E16.8)') dfloat(j),eigens_up(i,j)+E_f  
     end do    
     if(nkpt==1) then
       eigens_up(:,2)=eigens_up(:,1)
       do j=2,2
          write(1,'(2E16.8)') dfloat(j),eigens_up(i,j)+E_f  
       end do    
     end if    
     write(1,'(a,i0)') '&'    
  end do
else
  do i=1,nband    
     write(1,'(a,i0,a)') '@    s',i-1,' line linewidth 2.0' 
     write(1,'(a,i0,a)') '@    s',i-1,' line color 1' 
  end do   
  
  do i=1,nband
     write(1,'(a,i0)') '@target G0.S',i-1    
     write(1,'(a)') '@type xy'    
     do j=1,nkpt
        write(1,'(2E16.8)') dfloat(j),eigens_up(i,j)+E_f 
     end do 
     if(nkpt==1) then
       eigens_up(:,2)=eigens_up(:,1)
       do j=2,2
          write(1,'(2E16.8)') dfloat(j),eigens_up(i,j)+E_f   
       end do    
     end if         
     write(1,'(a,i0)') '&'    
  end do  
  
  do i=1+nband,2*nband
     write(1,'(a,i0,a)') '@    s',i-1,' line linewidth 2.0' 
     write(1,'(a,i0,a)') '@    s',i-1,' line color 2' 
  end do  
  
  
  do i=1+nband,2*nband
     write(1,'(a,i0)') '@target G0.S',i-1    
     write(1,'(a)') '@type xy'    
     do j=1,nkpt
        write(1,'(2E16.8)') dfloat(j),eigens_dw(i-nband,j)+E_f  
     end do 
     if(nkpt==1) then
       eigens_dw(:,2)=eigens_dw(:,1)
       do j=2,2
          write(1,'(2E16.8)') dfloat(j),eigens_dw(i-nband,j)+E_f 
       end do    
     end if         
     write(1,'(a,i0)') '&'    
  end do  
end if

close(1)

if(nspin==2) then

open(unit=1,file='eigenvalue_up_nonshift.agr')
!
! Xmgrace format 
!    
write(1,'(a)') '# Grace project file                      '    
write(1,'(a)') '@version 50113                            '    
write(1,'(a)') '@page size 792, 612                       '    
write(1,'(a)') '@page scroll 5%                           '    
write(1,'(a)') '@page inout 5%                            '    
write(1,'(a)') '@link page off                            '    
write(1,'(a)') '@with g0'                                      
write(1,'(a)') '@    world xmin 1.00'    
write(1,'(a,f10.5)') '@    world xmax ',dfloat(nkpt)
write(1,'(a,f10.5)') '@    world ymin ',-6.0
write(1,'(a,f10.5)') '@    world ymax ',2.0
write(1,'(a)') '@    view 0.150000, 0.150000, 0.500000, 0.800000'
write(1,'(a)') '@default linewidth 1.5'  
write(1,'(a)') '@    xaxis  on'
write(1,'(a)') '@    xaxis  type zero false'
write(1,'(a)') '@    xaxis  offset 0.000000 , 0.000000'
write(1,'(a)') '@    xaxis  bar on'
write(1,'(a)') '@    xaxis  bar color 1'
write(1,'(a)') '@    xaxis  bar linestyle 1'
write(1,'(a)') '@    xaxis  bar linewidth 1.0'
write(1,'(a)') '@    xaxis  label ""'
write(1,'(a)') '@    xaxis  label layout para'
write(1,'(a)') '@    xaxis  label place auto'
write(1,'(a)') '@    xaxis  label char size 1.070000'
write(1,'(a)') '@    xaxis  label font 9'
write(1,'(a)') '@    xaxis  label color 1'
write(1,'(a)') '@    xaxis  label place normal'
write(1,'(a)') '@    xaxis  tick off'
write(1,'(a,i0)') '@    xaxis  tick major ',int(nkpt/5)
write(1,'(a)') '@    xaxis  tick minor ticks 1'
write(1,'(a)') '@    xaxis  tick default 6'
write(1,'(a)') '@    xaxis  tick place rounded true'
write(1,'(a)') '@    xaxis  tick in'
write(1,'(a)') '@    xaxis  tick major size 1.000000'
write(1,'(a)') '@    xaxis  tick major color 1'
write(1,'(a)') '@    xaxis  tick major linewidth 1.0'
write(1,'(a)') '@    xaxis  tick major linestyle 3'
! write(1,'(a)') '@    xaxis  tick major grid on'
write(1,'(a)') '@    xaxis  tick minor color 1'
write(1,'(a)') '@    xaxis  tick minor linewidth 1.0'
write(1,'(a)') '@    xaxis  tick minor linestyle 1'
write(1,'(a)') '@    xaxis  tick minor grid off'
write(1,'(a)') '@    xaxis  tick minor size 0.500000'
write(1,'(a)') '@    xaxis  ticklabel off'
write(1,'(a)') '@    xaxis  ticklabel format general'
write(1,'(a)') '@    xaxis  ticklabel prec 5'
write(1,'(a)') '@    xaxis  ticklabel formula ""'
write(1,'(a)') '@    xaxis  ticklabel append ""'
write(1,'(a)') '@    xaxis  ticklabel prepend ""'
write(1,'(a)') '@    xaxis  ticklabel angle 0'
write(1,'(a)') '@    xaxis  ticklabel skip 0'
write(1,'(a)') '@    xaxis  ticklabel stagger 0'
write(1,'(a)') '@    xaxis  ticklabel place normal'
write(1,'(a)') '@    xaxis  ticklabel offset auto'
write(1,'(a)') '@    xaxis  ticklabel offset 0.000000 , 0.010000'
write(1,'(a)') '@    xaxis  ticklabel start type auto'
write(1,'(a)') '@    xaxis  ticklabel start 0.000000'
write(1,'(a)') '@    xaxis  ticklabel stop type auto'
write(1,'(a)') '@    xaxis  ticklabel stop 0.000000'
write(1,'(a)') '@    xaxis  ticklabel char size 1.230000'
write(1,'(a)') '@    xaxis  ticklabel font 9'
write(1,'(a)') '@    xaxis  ticklabel color 1'
write(1,'(a)') '@    xaxis  tick place normal'
! write(1,'(a)') '@    xaxis  tick spec type both'
! write(1,'(a,i0)') '@    xaxis  tick spec ',nlines+1   
! write(1,'(a)') '@    xaxis  tick major 0, 0'    
! do i=1,nlines   
!    write(1,'(a,i0,a,a)') '@    xaxis  ticklabel ',i-1,',', '"'//trim(adjustl(xlabel(i)))//'"'    
!    write(1,'(a,i0,a,f10.5)') '@    xaxis  tick major ',i,' , ',sum(kpath_len(1:i))    
! end do    
! write(1,'(a,i0,a)') '@    xaxis  ticklabel ',nlines  &    
!      ,',"'//trim(adjustl(xlabel(nlines+1)))//'"'    
write(1,'(a)') '@    yaxis  on'
write(1,'(a)') '@    yaxis  type zero false'
write(1,'(a)') '@    yaxis  offset 0.000000 , 0.000000'
write(1,'(a)') '@    yaxis  bar on'
write(1,'(a)') '@    yaxis  bar color 1'
write(1,'(a)') '@    yaxis  bar linestyle 1'
write(1,'(a)') '@    yaxis  bar linewidth 1.0'
write(1,'(a)') '@    yaxis  label "Energy (eV)"'
write(1,'(a)') '@    yaxis  label layout para'
write(1,'(a)') '@    yaxis  label place spec'
write(1,'(a)') '@    yaxis  label place 0.000000, 0.060000'
write(1,'(a)') '@    yaxis  label char size 1.230000'
write(1,'(a)') '@    yaxis  label font 9'
write(1,'(a)') '@    yaxis  label color 1'
write(1,'(a)') '@    yaxis  label place normal'
write(1,'(a)') '@    yaxis  tick on'
write(1,'(a)') '@    yaxis  tick major 1'
write(1,'(a)') '@    yaxis  tick minor ticks 1'
write(1,'(a)') '@    yaxis  tick default 6'
write(1,'(a)') '@    yaxis  tick place rounded true'
write(1,'(a)') '@    yaxis  tick out'
write(1,'(a)') '@    yaxis  tick major size 0.830000'
write(1,'(a)') '@    yaxis  tick major color 1'
write(1,'(a)') '@    yaxis  tick major linewidth 1.5'
write(1,'(a)') '@    yaxis  tick major linestyle 1'
write(1,'(a)') '@    yaxis  tick major grid off'
write(1,'(a)') '@    yaxis  tick minor color 1'
write(1,'(a)') '@    yaxis  tick minor linewidth 1.5'
write(1,'(a)') '@    yaxis  tick minor linestyle 1'
write(1,'(a)') '@    yaxis  tick minor grid off'
write(1,'(a)') '@    yaxis  tick minor size 0.410000'
write(1,'(a)') '@    yaxis  ticklabel on'
write(1,'(a)') '@    yaxis  ticklabel format general'
write(1,'(a)') '@    yaxis  ticklabel prec 5'
write(1,'(a)') '@    yaxis  ticklabel formula ""'
write(1,'(a)') '@    yaxis  ticklabel append ""'
write(1,'(a)') '@    yaxis  ticklabel prepend ""'
write(1,'(a)') '@    yaxis  ticklabel angle 0'
write(1,'(a)') '@    yaxis  ticklabel skip 0'
write(1,'(a)') '@    yaxis  ticklabel stagger 0'
write(1,'(a)') '@    yaxis  ticklabel place normal'
write(1,'(a)') '@    yaxis  ticklabel offset auto'
write(1,'(a)') '@    yaxis  ticklabel offset 0.000000 , 0.010000'
write(1,'(a)') '@    yaxis  ticklabel start type auto'
write(1,'(a)') '@    yaxis  ticklabel start 0.000000'
write(1,'(a)') '@    yaxis  ticklabel stop type auto'
write(1,'(a)') '@    yaxis  ticklabel stop 0.000000'
write(1,'(a)') '@    yaxis  ticklabel char size 1.110000'
write(1,'(a)') '@    yaxis  ticklabel font 9'
write(1,'(a)') '@    yaxis  ticklabel color 1'
write(1,'(a)') '@    yaxis  tick place normal'
write(1,'(a)') '@    yaxis  tick spec type none'
write(1,'(a)') '@    altxaxis  off'
write(1,'(a)') '@    altyaxis  off'
write(1,'(a)') '@    legend on'
write(1,'(a)') '@    legend loctype view'
write(1,'(a)') '@    legend 0.85, 0.8'
write(1,'(a)') '@    legend box color 1'
write(1,'(a)') '@    legend box pattern 1'
write(1,'(a)') '@    legend box linewidth 1.5'
write(1,'(a)') '@    legend box linestyle 1'
write(1,'(a)') '@    legend box fill color 0'
write(1,'(a)') '@    legend box fill pattern 1'
write(1,'(a)') '@    legend font 9'
write(1,'(a)') '@    legend char size 1.000000'
write(1,'(a)') '@    legend color 1'
write(1,'(a)') '@    legend length 4'
write(1,'(a)') '@    legend vgap 1'
write(1,'(a)') '@    legend hgap 1'
write(1,'(a)') '@    legend invert false'
write(1,'(a)') '@    frame type 0'
write(1,'(a)') '@    frame linestyle 1'
write(1,'(a)') '@    frame linewidth 1.5'
write(1,'(a)') '@    frame color 1'
write(1,'(a)') '@    frame pattern 1'
write(1,'(a)') '@    frame background color 0'
write(1,'(a)') '@    frame background pattern 0'



  do i=1,nband    
     write(1,'(a,i0,a)') '@    s',i-1,' line linewidth 2.0' 
     write(1,'(a,i0,a)') '@    s',i-1,' line color 1' 
  end do   
  
  do i=1,nband
     write(1,'(a,i0)') '@target G0.S',i-1    
     write(1,'(a)') '@type xy'    
     do j=1,nkpt
        write(1,'(2E16.8)') dfloat(j),eigens_up(i,j)+E_f 
   end do    
     write(1,'(a,i0)') '&'    
  end do 
  
close(1)

open(unit=1,file='eigenvalue_dw_nonshift.agr')
!
! Xmgrace format 
!    
write(1,'(a)') '# Grace project file                      '    
write(1,'(a)') '@version 50113                            '    
write(1,'(a)') '@page size 792, 612                       '    
write(1,'(a)') '@page scroll 5%                           '    
write(1,'(a)') '@page inout 5%                            '    
write(1,'(a)') '@link page off                            '    
write(1,'(a)') '@with g0'                                      
write(1,'(a)') '@    world xmin 1.00'    
write(1,'(a,f10.5)') '@    world xmax ',dfloat(nkpt)
write(1,'(a,f10.5)') '@    world ymin ',-6.0
write(1,'(a,f10.5)') '@    world ymax ',2.0
write(1,'(a)') '@    view 0.150000, 0.150000, 0.500000, 0.800000'
write(1,'(a)') '@default linewidth 1.5'  
write(1,'(a)') '@    xaxis  on'
write(1,'(a)') '@    xaxis  type zero false'
write(1,'(a)') '@    xaxis  offset 0.000000 , 0.000000'
write(1,'(a)') '@    xaxis  bar on'
write(1,'(a)') '@    xaxis  bar color 1'
write(1,'(a)') '@    xaxis  bar linestyle 1'
write(1,'(a)') '@    xaxis  bar linewidth 1.0'
write(1,'(a)') '@    xaxis  label ""'
write(1,'(a)') '@    xaxis  label layout para'
write(1,'(a)') '@    xaxis  label place auto'
write(1,'(a)') '@    xaxis  label char size 1.070000'
write(1,'(a)') '@    xaxis  label font 9'
write(1,'(a)') '@    xaxis  label color 1'
write(1,'(a)') '@    xaxis  label place normal'
write(1,'(a)') '@    xaxis  tick off'
write(1,'(a,i0)') '@    xaxis  tick major ',int(nkpt/5)
write(1,'(a)') '@    xaxis  tick minor ticks 1'
write(1,'(a)') '@    xaxis  tick default 6'
write(1,'(a)') '@    xaxis  tick place rounded true'
write(1,'(a)') '@    xaxis  tick in'
write(1,'(a)') '@    xaxis  tick major size 1.000000'
write(1,'(a)') '@    xaxis  tick major color 1'
write(1,'(a)') '@    xaxis  tick major linewidth 1.0'
write(1,'(a)') '@    xaxis  tick major linestyle 3'
! write(1,'(a)') '@    xaxis  tick major grid on'
write(1,'(a)') '@    xaxis  tick minor color 1'
write(1,'(a)') '@    xaxis  tick minor linewidth 1.0'
write(1,'(a)') '@    xaxis  tick minor linestyle 1'
write(1,'(a)') '@    xaxis  tick minor grid off'
write(1,'(a)') '@    xaxis  tick minor size 0.500000'
write(1,'(a)') '@    xaxis  ticklabel off'
write(1,'(a)') '@    xaxis  ticklabel format general'
write(1,'(a)') '@    xaxis  ticklabel prec 5'
write(1,'(a)') '@    xaxis  ticklabel formula ""'
write(1,'(a)') '@    xaxis  ticklabel append ""'
write(1,'(a)') '@    xaxis  ticklabel prepend ""'
write(1,'(a)') '@    xaxis  ticklabel angle 0'
write(1,'(a)') '@    xaxis  ticklabel skip 0'
write(1,'(a)') '@    xaxis  ticklabel stagger 0'
write(1,'(a)') '@    xaxis  ticklabel place normal'
write(1,'(a)') '@    xaxis  ticklabel offset auto'
write(1,'(a)') '@    xaxis  ticklabel offset 0.000000 , 0.010000'
write(1,'(a)') '@    xaxis  ticklabel start type auto'
write(1,'(a)') '@    xaxis  ticklabel start 0.000000'
write(1,'(a)') '@    xaxis  ticklabel stop type auto'
write(1,'(a)') '@    xaxis  ticklabel stop 0.000000'
write(1,'(a)') '@    xaxis  ticklabel char size 1.230000'
write(1,'(a)') '@    xaxis  ticklabel font 9'
write(1,'(a)') '@    xaxis  ticklabel color 1'
write(1,'(a)') '@    xaxis  tick place normal'
! write(1,'(a)') '@    xaxis  tick spec type both'
! write(1,'(a,i0)') '@    xaxis  tick spec ',nlines+1   
! write(1,'(a)') '@    xaxis  tick major 0, 0'    
! do i=1,nlines   
!    write(1,'(a,i0,a,a)') '@    xaxis  ticklabel ',i-1,',', '"'//trim(adjustl(xlabel(i)))//'"'    
!    write(1,'(a,i0,a,f10.5)') '@    xaxis  tick major ',i,' , ',sum(kpath_len(1:i))    
! end do    
! write(1,'(a,i0,a)') '@    xaxis  ticklabel ',nlines  &    
!      ,',"'//trim(adjustl(xlabel(nlines+1)))//'"'    
write(1,'(a)') '@    yaxis  on'
write(1,'(a)') '@    yaxis  type zero false'
write(1,'(a)') '@    yaxis  offset 0.000000 , 0.000000'
write(1,'(a)') '@    yaxis  bar on'
write(1,'(a)') '@    yaxis  bar color 1'
write(1,'(a)') '@    yaxis  bar linestyle 1'
write(1,'(a)') '@    yaxis  bar linewidth 1.0'
write(1,'(a)') '@    yaxis  label "Energy (eV)"'
write(1,'(a)') '@    yaxis  label layout para'
write(1,'(a)') '@    yaxis  label place spec'
write(1,'(a)') '@    yaxis  label place 0.000000, 0.060000'
write(1,'(a)') '@    yaxis  label char size 1.230000'
write(1,'(a)') '@    yaxis  label font 9'
write(1,'(a)') '@    yaxis  label color 1'
write(1,'(a)') '@    yaxis  label place normal'
write(1,'(a)') '@    yaxis  tick on'
write(1,'(a)') '@    yaxis  tick major 1'
write(1,'(a)') '@    yaxis  tick minor ticks 1'
write(1,'(a)') '@    yaxis  tick default 6'
write(1,'(a)') '@    yaxis  tick place rounded true'
write(1,'(a)') '@    yaxis  tick out'
write(1,'(a)') '@    yaxis  tick major size 0.830000'
write(1,'(a)') '@    yaxis  tick major color 1'
write(1,'(a)') '@    yaxis  tick major linewidth 1.5'
write(1,'(a)') '@    yaxis  tick major linestyle 1'
write(1,'(a)') '@    yaxis  tick major grid off'
write(1,'(a)') '@    yaxis  tick minor color 1'
write(1,'(a)') '@    yaxis  tick minor linewidth 1.5'
write(1,'(a)') '@    yaxis  tick minor linestyle 1'
write(1,'(a)') '@    yaxis  tick minor grid off'
write(1,'(a)') '@    yaxis  tick minor size 0.410000'
write(1,'(a)') '@    yaxis  ticklabel on'
write(1,'(a)') '@    yaxis  ticklabel format general'
write(1,'(a)') '@    yaxis  ticklabel prec 5'
write(1,'(a)') '@    yaxis  ticklabel formula ""'
write(1,'(a)') '@    yaxis  ticklabel append ""'
write(1,'(a)') '@    yaxis  ticklabel prepend ""'
write(1,'(a)') '@    yaxis  ticklabel angle 0'
write(1,'(a)') '@    yaxis  ticklabel skip 0'
write(1,'(a)') '@    yaxis  ticklabel stagger 0'
write(1,'(a)') '@    yaxis  ticklabel place normal'
write(1,'(a)') '@    yaxis  ticklabel offset auto'
write(1,'(a)') '@    yaxis  ticklabel offset 0.000000 , 0.010000'
write(1,'(a)') '@    yaxis  ticklabel start type auto'
write(1,'(a)') '@    yaxis  ticklabel start 0.000000'
write(1,'(a)') '@    yaxis  ticklabel stop type auto'
write(1,'(a)') '@    yaxis  ticklabel stop 0.000000'
write(1,'(a)') '@    yaxis  ticklabel char size 1.110000'
write(1,'(a)') '@    yaxis  ticklabel font 9'
write(1,'(a)') '@    yaxis  ticklabel color 1'
write(1,'(a)') '@    yaxis  tick place normal'
write(1,'(a)') '@    yaxis  tick spec type none'
write(1,'(a)') '@    altxaxis  off'
write(1,'(a)') '@    altyaxis  off'
write(1,'(a)') '@    legend on'
write(1,'(a)') '@    legend loctype view'
write(1,'(a)') '@    legend 0.85, 0.8'
write(1,'(a)') '@    legend box color 1'
write(1,'(a)') '@    legend box pattern 1'
write(1,'(a)') '@    legend box linewidth 1.5'
write(1,'(a)') '@    legend box linestyle 1'
write(1,'(a)') '@    legend box fill color 0'
write(1,'(a)') '@    legend box fill pattern 1'
write(1,'(a)') '@    legend font 9'
write(1,'(a)') '@    legend char size 1.000000'
write(1,'(a)') '@    legend color 1'
write(1,'(a)') '@    legend length 4'
write(1,'(a)') '@    legend vgap 1'
write(1,'(a)') '@    legend hgap 1'
write(1,'(a)') '@    legend invert false'
write(1,'(a)') '@    frame type 0'
write(1,'(a)') '@    frame linestyle 1'
write(1,'(a)') '@    frame linewidth 1.5'
write(1,'(a)') '@    frame color 1'
write(1,'(a)') '@    frame pattern 1'
write(1,'(a)') '@    frame background color 0'
write(1,'(a)') '@    frame background pattern 0'



  do i=1,nband    
     write(1,'(a,i0,a)') '@    s',i-1,' line linewidth 2.0' 
     write(1,'(a,i0,a)') '@    s',i-1,' line color 2' 
  end do   
  
  do i=1,nband
     write(1,'(a,i0)') '@target G0.S',i-1    
     write(1,'(a)') '@type xy'    
     do j=1,nkpt
        write(1,'(2E16.8)') dfloat(j),eigens_dw(i,j)+E_f 
   end do    
     write(1,'(a,i0)') '&'    
  end do 
  
close(1)    
  
end if



end program toband

